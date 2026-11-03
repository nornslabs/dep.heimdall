"""Transactional use cases for draft approval."""

import hashlib
import json
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.outbox.writer import write_outbox
from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.approval._delivery import get_approved_draft
from heimdall.modules.approval.domain import ApprovalRef, validate_approval
from heimdall.modules.approval.models import Approval
from heimdall.modules.approval.workflow_models import WorkflowDispatchApproval
from heimdall.modules.audit.api import AuditEntry, append_in_transaction, pr_subject
from heimdall.modules.desk.api import get_draft_in_transaction, hash_draft
from heimdall.modules.tenancy.api import (
    ConfigFileReader,
    get_repo_id_in_transaction,
    load_config_in_transaction,
    repo_auto_suggest_paused,
    set_auto_suggest_paused_in_transaction,
)
from heimdall.modules.triage.api import get_pr_snapshot
from heimdall.shared.context import PrRef, TenantCtx
from heimdall.shared.errors import DraftChanged, NotAMaintainer
from heimdall.shared.github import RepoRef

REPRO_DISPATCH_POLICY = "policy:repro_dispatch"


async def queue_repro_workflow_dispatch(
    session: AsyncSession,
    ctx: TenantCtx,
    run_id: uuid.UUID,
    repo_full_name: str,
    workflow: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
    workflow_ref: str | None,
    test_cmd: str,
) -> None:
    """Authorize and queue the exact reproduction workflow request atomically.

    The reproduction service calls this only after validating the current PR head.
    The fixed policy identity records why this non-comment GitHub write is allowed.
    """
    inputs = {
        "repro_run_id": str(run_id),
        "pr_number": str(pr_number),
        "head_sha": head_sha,
        "base_sha": base_sha,
        "test_cmd": test_cmd,
    }
    if not workflow_ref:
        raise ValueError("PR base branch is required for workflow dispatch")
    request = {
        "repo_full_name": repo_full_name,
        "workflow": workflow,
        "ref": workflow_ref,
        "inputs": inputs,
    }
    request_hash = hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    authorization = WorkflowDispatchApproval(
        installation_id=ctx.installation_id,
        repro_run_id=run_id,
        request_hash=request_hash,
        approver=REPRO_DISPATCH_POLICY,
    )
    session.add(authorization)
    await session.flush()
    repo_id = await get_repo_id_in_transaction(session, ctx, repo_full_name)
    await append_in_transaction(
        session,
        ctx,
        AuditEntry(
            actor=REPRO_DISPATCH_POLICY,
            action="repro.dispatch_authorized",
            subject=pr_subject(PrRef(repo_id, pr_number)),
            payload={
                "repro_run_id": str(run_id),
                "approval_id": str(authorization.id),
                "request_hash": request_hash,
                "head_sha": head_sha,
            },
        ),
    )
    await write_outbox(
        session,
        installation_id=ctx.installation_id,
        channel="github",
        kind="github.workflow_dispatch",
        payload={**request, "approval_id": str(authorization.id), "request_hash": request_hash},
        idempotency_key=f"github:repro-dispatch:{run_id}",
    )


async def verify_repro_workflow_dispatch(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    approval_id: uuid.UUID,
    run_id: uuid.UUID,
    request_hash: str,
) -> bool:
    """Confirm the tenant scoped policy approval matches a queued request."""
    async with unit_of_work(sessions) as session:
        result = await session.exec(
            select_for_tenant(WorkflowDispatchApproval, ctx.installation_id).where(
                WorkflowDispatchApproval.id == approval_id,
                WorkflowDispatchApproval.repro_run_id == run_id,
                WorkflowDispatchApproval.request_hash == request_hash,
                WorkflowDispatchApproval.approver == REPRO_DISPATCH_POLICY,
            )
        )
        return result.first() is not None


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
        if draft.superseded_by is not None or draft.body_hash != hash_draft(draft.body):
            raise DraftChanged("draft changed before approval")
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
        pr_ref = PrRef(snapshot.repo_id, snapshot.number)
        await _audit_approval(session, ctx, pr_ref, draft_id, approver, draft_hash, via)
        await _enqueue_approved_comment(session, ctx, repo, snapshot.number, draft_id, draft_hash)
        return ApprovalRef(approval.id, draft_id, approver, draft_hash, via)


async def approve_by_policy(
    sessions: async_sessionmaker[AsyncSession],
    reader: ConfigFileReader,
    ctx: TenantCtx,
    repo: RepoRef,
    draft_id: uuid.UUID,
    category: str,
) -> ApprovalRef:
    """Approve an exact mechanical draft only when repository policy allows its category.

    Args:
        sessions: Tenant database session factory.
        reader: Reader for the repository's current maintainer and auto-suggest policy.
        ctx: Tenant and request context.
        repo: Repository containing the draft's pull request.
        draft_id: Saved mechanical draft being approved.
        category: Mechanical finding category that produced the draft.

    Returns:
        The saved policy approval and exact approved hash.

    Raises:
        DraftChanged: The saved draft was superseded or its hash changed.
        ValueError: The repository policy does not permit automatic approval.
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
        paused = await repo_auto_suggest_paused(session, ctx, repo_id)
        if (
            paused
            or not config.auto_suggest.enabled
            or category not in config.auto_suggest.categories
        ):
            raise ValueError("automatic approval is disabled by repository policy")
        expected_hash = hash_draft(draft.body)
        if draft.superseded_by is not None or draft.body_hash != expected_hash:
            raise DraftChanged("mechanical draft changed before policy approval")
        return await _persist_approval(
            session,
            ctx,
            repo,
            PrRef(snapshot.repo_id, snapshot.number),
            draft_id,
            "policy:auto_suggest",
            expected_hash,
            "policy:auto_suggest",
        )


async def pause_auto_suggestions(
    sessions: async_sessionmaker[AsyncSession],
    reader: ConfigFileReader,
    ctx: TenantCtx,
    repo: RepoRef,
    draft_id: uuid.UUID,
    approver: str,
) -> None:
    """Let a configured maintainer pause automatic suggestions for a repository.

    Args:
        sessions: Tenant database session factory.
        reader: Reader for repository maintainer policy.
        ctx: Tenant and request context.
        repo: Repository whose policy is being paused.
        draft_id: Draft message whose thread received the command.
        approver: Switch username mapped to a GitHub login.

    Raises:
        NotAMaintainer: The user is not configured as a repository maintainer.
        ValueError: The draft or repository does not belong to this installation.
    """
    async with unit_of_work(sessions) as session:
        draft = await get_draft_in_transaction(session, ctx, draft_id)
        if draft.superseded_by is not None or draft.body_hash != hash_draft(draft.body):
            raise DraftChanged("draft changed before the pause command")
        snapshot = await get_pr_snapshot(session, ctx, draft.pr_id)
        if snapshot is None:
            raise ValueError("draft pull request is not available to this installation")
        repo_id = await get_repo_id_in_transaction(session, ctx, repo.full_name)
        if snapshot.repo_id != repo_id:
            raise ValueError("draft pull request is not in the requested repository")
        config = await load_config_in_transaction(session, reader, ctx, repo, snapshot.head_sha)
        maintainers = {login.casefold() for login in config.maintainers}
        if approver.casefold() not in maintainers:
            raise NotAMaintainer(f"{approver} is not a configured maintainer")
        await set_auto_suggest_paused_in_transaction(session, ctx, repo_id, True)
        await append_in_transaction(
            session,
            ctx,
            AuditEntry(
                actor=approver,
                action="auto_suggest.paused",
                subject=pr_subject(PrRef(snapshot.repo_id, snapshot.number)),
                payload={"draft_id": str(draft_id)},
            ),
        )


async def _persist_approval(
    session: AsyncSession,
    ctx: TenantCtx,
    repo: RepoRef,
    pr_ref: PrRef,
    draft_id: uuid.UUID,
    approver: str,
    draft_hash: str,
    via: str,
) -> ApprovalRef:
    """Store one approval, its audit entry and its idempotent GitHub delivery request."""
    existing_result = await session.exec(
        select_for_tenant(Approval, ctx.installation_id)
        .where(Approval.draft_id == draft_id)
        .with_for_update()
    )
    existing = existing_result.first()
    if existing is not None:
        if existing.draft_hash != draft_hash:
            raise DraftChanged("draft already has an approval for a different hash")
        await _enqueue_approved_comment(session, ctx, repo, pr_ref.number, draft_id, draft_hash)
        return ApprovalRef(
            existing.id, existing.draft_id, existing.approver, existing.draft_hash, existing.via
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
    await _audit_approval(session, ctx, pr_ref, draft_id, approver, draft_hash, via)
    await _enqueue_approved_comment(session, ctx, repo, pr_ref.number, draft_id, draft_hash)
    return ApprovalRef(approval.id, draft_id, approver, draft_hash, via)


async def _audit_approval(
    session: AsyncSession,
    ctx: TenantCtx,
    pr_ref: PrRef,
    draft_id: uuid.UUID,
    approver: str,
    draft_hash: str,
    via: str,
) -> None:
    """Record who approved which exact draft text, in the approval's own transaction."""
    await append_in_transaction(
        session,
        ctx,
        AuditEntry(
            actor=approver,
            action="draft.approved",
            subject=pr_subject(pr_ref),
            payload={"draft_id": str(draft_id), "draft_hash": draft_hash, "via": via},
        ),
    )


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


__all__ = [
    "approve_by_policy",
    "get_approved_draft",
    "pause_auto_suggestions",
    "queue_repro_workflow_dispatch",
    "record_approval",
    "verify_repro_workflow_dispatch",
]
