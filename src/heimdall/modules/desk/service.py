"""Persist cards and approval-bound drafts."""

import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.desk.domain import (
    CardContent,
    CardRef,
    DraftRef,
    MechanicalTaskRef,
    hash_draft,
    render_card,
    render_mechanical_task_block,
)
from heimdall.modules.desk.models import AgentTask, Card, Draft
from heimdall.modules.review.api import FindingRef, get_finding_in_transaction
from heimdall.modules.triage.api import get_pr_snapshot
from heimdall.shared.context import TenantCtx
from heimdall.shared.errors import StaleHeadSha


async def save_card(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    pr_id: uuid.UUID,
    content: CardContent,
) -> CardRef:
    """Save or return the card for this PR head without posting it directly.

    Args:
        sessions: Tenant database session factory.
        ctx: Tenant and request context.
        pr_id: Triage PR row represented by the card.
        content: Rendered card fields for a specific PR head.

    Returns:
        Stable card reference and rendered body.

    Raises:
        StaleHeadSha: The PR no longer has the card's head commit.
    """
    body = render_card(content)
    async with unit_of_work(sessions) as session:
        pr = await get_pr_snapshot(session, ctx, pr_id)
        if pr is None or pr.head_sha != content.head_sha:
            raise StaleHeadSha("pull request head changed before its card was saved")
        result = await session.exec(
            select_for_tenant(Card, ctx.installation_id).where(
                Card.pr_id == pr_id,
                Card.head_sha == content.head_sha,
            )
        )
        card = result.first()
        if card is None:
            card = Card(
                installation_id=ctx.installation_id,
                pr_id=pr_id,
                head_sha=content.head_sha,
                body=body,
            )
            session.add(card)
            await session.flush()
        return CardRef(card.id, pr_id, card.head_sha, card.body)


async def save_draft(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    pr_id: uuid.UUID,
    head_sha: str,
    kind: str,
    body: str,
) -> DraftRef:
    """Save an exact draft and supersede older drafts of the same kind.

    Args:
        sessions: Tenant database session factory.
        ctx: Tenant and request context.
        pr_id: Triage PR row the draft belongs to.
        head_sha: PR head the draft was prepared against.
        kind: Draft category, such as review comment or summary.
        body: Exact text requiring maintainer approval.

    Returns:
        Draft reference including the approval hash.

    Raises:
        ValueError: Kind or body is empty.
        StaleHeadSha: The PR no longer has the draft's head commit.
    """
    if not kind.strip() or not body:
        raise ValueError("draft kind and body are required")
    body_hash = hash_draft(body)
    async with unit_of_work(sessions) as session:
        pr = await get_pr_snapshot(session, ctx, pr_id)
        if pr is None or pr.head_sha != head_sha:
            raise StaleHeadSha("pull request head changed before its draft was saved")
        result = await session.exec(
            select_for_tenant(Draft, ctx.installation_id).where(
                Draft.pr_id == pr_id,
                Draft.kind == kind,
                Draft.body_hash == body_hash,
                col(Draft.superseded_by).is_(None),
            )
        )
        existing = result.first()
        if existing is not None:
            return DraftRef(existing.id, pr_id, kind, existing.body, existing.body_hash)
        draft = Draft(
            id=uuid.uuid4(),
            installation_id=ctx.installation_id,
            pr_id=pr_id,
            kind=kind,
            body=body,
            body_hash=body_hash,
            head_sha=head_sha,
        )
        prior_drafts = await session.exec(
            select_for_tenant(Draft, ctx.installation_id).where(
                Draft.pr_id == pr_id,
                Draft.kind == kind,
                col(Draft.superseded_by).is_(None),
            )
        )
        for prior in prior_drafts:
            prior.superseded_by = draft.id
        session.add(draft)
        return DraftRef(draft.id, pr_id, kind, body, body_hash)


async def create_mechanical_task(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    pr_id: uuid.UUID,
    head_sha: str,
    finding_id: uuid.UUID,
) -> MechanicalTaskRef:
    """Create an approval-bound handoff for a current mechanical review finding.

    Args:
        sessions: Tenant database session factory.
        ctx: Tenant and request context.
        pr_id: Pull request receiving the task suggestion.
        head_sha: Exact PR head evaluated by the finding.
        finding_id: Saved finding to turn into a mechanical task.

    Returns:
        Existing or newly persisted task, suggestion block and draft hash.

    Raises:
        StaleHeadSha: PR head changed before task creation.
        ValueError: Finding is absent, belongs to another PR or is not mechanical.
    """
    async with unit_of_work(sessions) as session:
        snapshot = await get_pr_snapshot(session, ctx, pr_id)
        if snapshot is None or snapshot.head_sha != head_sha:
            raise StaleHeadSha("pull request head changed before mechanical task creation")
        finding = await get_finding_in_transaction(session, ctx, pr_id, finding_id)
        if finding is None or finding.mechanical_category is None:
            raise ValueError("finding is not a current mechanical finding for this pull request")
        return await _create_task_in_transaction(session, ctx, pr_id, snapshot.number, finding)


async def _create_task_in_transaction(
    session: AsyncSession,
    ctx: TenantCtx,
    pr_id: uuid.UUID,
    pr_number: int,
    finding: FindingRef,
) -> MechanicalTaskRef:
    """Save or return one task and its approval-bound draft atomically."""
    existing_result = await session.exec(
        select_for_tenant(AgentTask, ctx.installation_id).where(AgentTask.finding_id == finding.id)
    )
    existing = existing_result.first()
    if existing is not None:
        draft = await get_draft_in_transaction(session, ctx, existing.draft_id)
        task_block = _task_block(existing.id, finding)
        return _mechanical_task_ref(existing, finding, task_block, draft.body_hash)
    task_id = await _next_task_id(session, ctx, pr_id, pr_number)
    task_block = _task_block(task_id, finding)
    draft = Draft(
        id=uuid.uuid4(),
        installation_id=ctx.installation_id,
        pr_id=pr_id,
        kind=f"mechanical_task:{task_id}",
        body=task_block,
        body_hash=hash_draft(task_block),
    )
    session.add(draft)
    await session.flush()
    task = AgentTask(
        id=task_id,
        installation_id=ctx.installation_id,
        pr_id=pr_id,
        finding_id=finding.id,
        draft_id=draft.id,
        status="suggested",
    )
    session.add(task)
    await session.flush()
    return _mechanical_task_ref(task, finding, task_block, draft.body_hash)


async def _next_task_id(
    session: AsyncSession, ctx: TenantCtx, pr_id: uuid.UUID, pr_number: int
) -> str:
    """Assign the next readable task number within one pull request."""
    result = await session.exec(
        select_for_tenant(AgentTask, ctx.installation_id).where(AgentTask.pr_id == pr_id)
    )
    return f"HT-{pr_number}-{len(result.all()) + 1}"


def _task_block(task_id: str, finding: FindingRef) -> str:
    """Render the exact body stored for an approval-bound mechanical task."""
    category = finding.mechanical_category
    if category is None:
        raise ValueError("finding is not marked as mechanical")
    return render_mechanical_task_block(
        task_id, finding.id, finding.file, finding.line, category, finding.text
    )


def _mechanical_task_ref(
    task: AgentTask, finding: FindingRef, task_block: str, body_hash: str
) -> MechanicalTaskRef:
    """Build the public task value from its row and validated current finding."""
    return MechanicalTaskRef(
        id=task.id,
        pr_id=task.pr_id,
        finding_id=task.finding_id,
        draft_id=task.draft_id,
        category=finding.mechanical_category or "",
        suggestion=finding.text,
        task_block=task_block,
        body_hash=body_hash,
    )


__all__ = [
    "create_mechanical_task",
    "get_current_draft_in_transaction",
    "get_draft",
    "get_draft_in_transaction",
    "save_card",
    "save_draft",
]


async def get_current_draft_in_transaction(
    session: AsyncSession, ctx: TenantCtx, pr_id: uuid.UUID, kind: str
) -> DraftRef:
    """Load the un-superseded draft for a pull request and kind.

    Args:
        session: Session inside the caller's transaction.
        ctx: Tenant and request context.
        pr_id: Pull request identifier.
        kind: Draft category, such as "switch_card".

    Returns:
        Exact draft text and stored approval hash.

    Raises:
        ValueError: No current draft of this kind exists.
    """
    result = await session.exec(
        select_for_tenant(Draft, ctx.installation_id).where(
            Draft.pr_id == pr_id,
            Draft.kind == kind,
            col(Draft.superseded_by).is_(None),
        )
    )
    draft = result.first()
    if draft is None:
        raise ValueError(f"no current {kind} draft registered for PR {pr_id}")
    return DraftRef(
        draft.id,
        draft.pr_id,
        draft.kind,
        draft.body,
        draft.body_hash,
        draft.superseded_by,
        draft.head_sha,
    )


async def get_latest_draft(
    sessions: async_sessionmaker[AsyncSession], ctx: TenantCtx, pr_id: uuid.UUID
) -> DraftRef | None:
    """Return the newest un-superseded draft of a pull request, whatever its kind.

    Args:
        sessions: Tenant database session factory.
        ctx: Tenant and request context; the query is scoped to its installation.
        pr_id: Pull request identifier.

    Returns:
        The draft a maintainer's go-ahead would approve, or None when there is none.
    """
    async with unit_of_work(sessions) as session:
        result = await session.exec(
            select_for_tenant(Draft, ctx.installation_id)
            .where(Draft.pr_id == pr_id, col(Draft.superseded_by).is_(None))
            .order_by(col(Draft.created_at).desc())
        )
        draft = result.first()
    if draft is None:
        return None
    return DraftRef(draft.id, draft.pr_id, draft.kind, draft.body, draft.body_hash, None)


async def get_draft(
    sessions: async_sessionmaker[AsyncSession], ctx: TenantCtx, draft_id: uuid.UUID
) -> DraftRef:
    """Load one draft through its owning tenant.

    Args:
        sessions: Tenant database session factory.
        ctx: Tenant and request context.
        draft_id: Draft to retrieve.

    Returns:
        Exact draft text and its stored approval hash.

    Raises:
        ValueError: The draft is not registered for this installation.
    """
    async with unit_of_work(sessions) as session:
        return await get_draft_in_transaction(session, ctx, draft_id)


async def get_draft_in_transaction(
    session: AsyncSession, ctx: TenantCtx, draft_id: uuid.UUID
) -> DraftRef:
    """Load one draft within the caller's transaction.

    Args:
        session: Session inside the caller's transaction.
        ctx: Tenant and request context.
        draft_id: Draft to retrieve.

    Returns:
        Exact draft text and stored approval hash.

    Raises:
        ValueError: The draft is not registered for this installation.
    """
    result = await session.exec(
        select_for_tenant(Draft, ctx.installation_id).where(Draft.id == draft_id)
    )
    draft = result.first()
    if draft is None:
        raise ValueError(f"draft {draft_id} is not registered for this installation")
    return DraftRef(
        draft.id,
        draft.pr_id,
        draft.kind,
        draft.body,
        draft.body_hash,
        draft.superseded_by,
        draft.head_sha,
    )
