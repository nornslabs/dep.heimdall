"""Save validated, head-scoped review findings."""

import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.review.domain import FindingBatch, FindingInput, validate_findings
from heimdall.modules.review.models import Finding
from heimdall.modules.review.ports import ReviewReader
from heimdall.modules.tenancy.api import ReviewConfig
from heimdall.modules.triage.api import (
    EventKind,
    Facts,
    PrSnapshot,
    get_pr_snapshot,
    on_step_done_in_transaction,
)
from heimdall.shared.context import TenantCtx
from heimdall.shared.errors import StaleHeadSha
from heimdall.shared.github import RepoRef


async def save_findings(
    sessions: async_sessionmaker[AsyncSession],
    reader: ReviewReader,
    ctx: TenantCtx,
    repo: RepoRef,
    pr_id: uuid.UUID,
    head_sha: str,
    event_id: str,
    findings: tuple[FindingInput, ...],
    config: ReviewConfig,
    *,
    repro_done: bool = False,
) -> FindingBatch:
    """Validate and save findings for the current PR head, then finish Loki's step.

    Args:
        sessions: Tenant database session factory.
        reader: GitHub reader used to validate changed file paths.
        ctx: Tenant and request context.
        repo: Repository containing the pull request.
        pr_id: Triage PR row id.
        head_sha: Commit reviewed by Loki.
        event_id: Stable job result idempotency key.
        findings: Untrusted proposed findings from Loki.
        config: Effective review limits and skipped paths.
        repro_done: Whether Huginn already finished on this head.

    Returns:
        Accepted and rejected findings in input order.

    Raises:
        StaleHeadSha: The PR head changed before findings were saved.
    """
    pr = await _read_current_pr(sessions, ctx, pr_id, head_sha)
    changed = await reader.list_changed_files(repo, pr.number)
    batch = validate_findings(findings, frozenset(file.path for file in changed), config)
    async with unit_of_work(sessions) as session:
        current = await get_pr_snapshot(session, ctx, pr_id)
        if current is None or current.head_sha != head_sha:
            raise StaleHeadSha("pull request head changed before findings were saved")
        await _insert_new_findings(session, ctx, pr_id, head_sha, batch)
        await on_step_done_in_transaction(
            session,
            ctx,
            pr_id,
            event_id,
            EventKind.REVIEW_FINISHED,
            head_sha,
            Facts(review_done=True, repro_done=repro_done),
        )
    return batch


async def _read_current_pr(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    pr_id: uuid.UUID,
    head_sha: str,
) -> PrSnapshot:
    """Read the tenant PR before querying GitHub and reject stale work early."""
    async with unit_of_work(sessions) as session:
        pr = await get_pr_snapshot(session, ctx, pr_id)
    if pr is None or pr.head_sha != head_sha:
        raise StaleHeadSha("pull request head changed before review")
    return pr


async def _insert_new_findings(
    session: AsyncSession,
    ctx: TenantCtx,
    pr_id: uuid.UUID,
    head_sha: str,
    batch: FindingBatch,
) -> None:
    """Insert accepted findings while making retried saves idempotent."""
    existing_result = await session.exec(
        select_for_tenant(Finding, ctx.installation_id).where(
            Finding.pr_id == pr_id,
            Finding.head_sha == head_sha,
        )
    )
    existing = {
        (finding.file, finding.line, finding.text, finding.severity) for finding in existing_result
    }
    for finding in batch.accepted:
        identity = (finding.file, finding.line, finding.text, finding.severity)
        if identity in existing:
            continue
        session.add(
            Finding(
                installation_id=ctx.installation_id,
                pr_id=pr_id,
                head_sha=head_sha,
                file=finding.file,
                line=finding.line,
                text=finding.text,
                severity=finding.severity,
                mechanical_category=finding.mechanical_category,
            )
        )
        existing.add(identity)
