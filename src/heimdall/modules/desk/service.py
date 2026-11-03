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
    hash_draft,
    render_card,
)
from heimdall.modules.desk.models import Card, Draft
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


__all__ = ["get_draft", "get_draft_in_transaction", "save_card", "save_draft"]


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
    )
