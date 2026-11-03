"""Transactional use cases for draft approval."""

import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.outbox.writer import write_outbox
from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.approval._delivery import get_approved_draft
from heimdall.modules.approval.domain import ApprovalRef, validate_approval
from heimdall.modules.approval.models import Approval
from heimdall.modules.desk.api import get_draft_in_transaction, hash_draft
from heimdall.modules.tenancy.api import (
    ConfigFileReader,
    get_repo_id_in_transaction,
    load_config_in_transaction,
)
from heimdall.modules.triage.api import get_pr_snapshot
from heimdall.shared.context import TenantCtx
from heimdall.shared.github import RepoRef


async def record_approval(
    sessions: async_sessionmaker[AsyncSession],
    reader: ConfigFileReader,
    ctx: TenantCtx,
    repo: RepoRef,
    draft_id: uuid.UUID,
    approver: str,
    draft_hash: str,
    *,
    via: str = "reaction",
) -> ApprovalRef:
    """Record approval only when a configured maintainer approves active exact text.

    Args:
        sessions: Tenant database session factory.
        reader: Config reader used to load repository maintainer policy.
        ctx: Tenant and request context.
        repo: Repository where the draft's PR belongs.
        draft_id: Draft being approved.
        approver: GitHub login reacting to the draft.
        draft_hash: Hash of the text the maintainer approved.
        via: Approval source, normally a maintainer reaction.

    Returns:
        The persisted approval identity and exact approved hash.

    Raises:
        ValueError: Draft, PR, or repository is not available to this tenant.
        NotAMaintainer: The login is not listed in repository config.
        DraftChanged: The hash is stale or this draft was superseded.
    """
    async with unit_of_work(sessions) as session:
        draft = await get_draft_in_transaction(session, ctx, draft_id)
        snapshot = await get_pr_snapshot(session, ctx, draft.pr_id)
        if snapshot is None:
            raise ValueError("draft pull request is not available to this installation")
        repo_id = await get_repo_id_in_transaction(session, ctx, repo.full_name)
        if snapshot.repo_id != repo_id:
            raise ValueError("draft pull request is not in the requested repository")
        config = await load_config_in_transaction(session, reader, ctx, repo, snapshot.head_sha)
        expected_hash = hash_draft(draft.body)
        validate_approval(
            approver,
            config.maintainers,
            expected_hash,
            draft_hash,
            superseded=draft.superseded_by is not None or draft.body_hash != expected_hash,
        )

        existing_result = await session.exec(
            select_for_tenant(Approval, ctx.installation_id)
            .where(Approval.draft_id == draft_id)
            .with_for_update()
        )
        existing = existing_result.first()
        if existing is not None:
            if existing.draft_hash != draft_hash:
                raise ValueError("draft already has an approval for a different hash")
            await _enqueue_approved_comment(
                session, ctx, repo, snapshot.number, draft_id, draft_hash
            )
            return ApprovalRef(
                existing.id,
                existing.draft_id,
                existing.approver,
                existing.draft_hash,
                existing.via,
            )
        approval = Approval(
            id=uuid.uuid4(),
            installation_id=ctx.installation_id,
            draft_id=draft_id,
            approver=approver,
            draft_hash=draft_hash,
            via=via,
        )
        session.add(approval)
        await session.flush()
        await _enqueue_approved_comment(session, ctx, repo, snapshot.number, draft_id, draft_hash)
        return ApprovalRef(approval.id, draft_id, approver, draft_hash, via)


async def _enqueue_approved_comment(
    session: AsyncSession,
    ctx: TenantCtx,
    repo: RepoRef,
    pr_number: int,
    draft_id: uuid.UUID,
    draft_hash: str,
) -> None:
    """Queue only the exact approved draft as an idempotent GitHub comment."""
    await write_outbox(
        session,
        installation_id=ctx.installation_id,
        channel="github",
        kind="github.pr_comment",
        payload={
            "draft_id": str(draft_id),
            "draft_hash": draft_hash,
            "repo_full_name": repo.full_name,
            "pr_number": pr_number,
        },
        idempotency_key=f"github:draft:{draft_id}",
    )


__all__ = ["get_approved_draft", "record_approval"]
