"""Transactional start, result, and timeout use cases for reproductions."""

import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.adapters.queue.enqueue import enqueue
from heimdall.modules.approval.api import queue_repro_workflow_dispatch
from heimdall.modules.repro.domain import (
    MAX_REPRO_RUNS,
    REPRO_TIMEOUT_SECONDS,
    ReproResult,
    ReproRunDetails,
    ReproRunRef,
    ReproStatus,
    RunOutcome,
    Verdict,
    derive_verdict,
)
from heimdall.modules.repro.models import ReproRun
from heimdall.modules.tenancy.api import get_repo_full_name_in_transaction
from heimdall.modules.triage.api import (
    EventKind,
    Facts,
    PrSnapshot,
    PrState,
    get_pr_snapshot,
    on_step_done_in_transaction,
)
from heimdall.shared.context import TenantCtx
from heimdall.shared.errors import ReproLimitReached, StaleHeadSha


async def start_run(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    pr_id: uuid.UUID,
    head_sha: str,
    test_cmd: str,
) -> ReproRunRef:
    """Create an attempt and enqueue execution and timeout jobs atomically.

    Args:
        sessions: Tenant database session factory.
        ctx: Tenant and request context.
        pr_id: Triage PR row to reproduce.
        head_sha: Current PR head commit.
        test_cmd: Validated command from repository config.

    Returns:
        Stable identity for the new queued attempt.

    Raises:
        StaleHeadSha: The PR is not checking this head.
        ReproLimitReached: The head already has the maximum attempts.
    """
    async with unit_of_work(sessions) as session:
        pr = await _current_pr(session, ctx, pr_id, head_sha)
        if pr is None or pr.state is not PrState.CHECKING:
            raise StaleHeadSha("pull request is not checking the requested head")
        attempts = await session.exec(
            select_for_tenant(ReproRun, ctx.installation_id).where(
                ReproRun.pr_id == pr_id,
                ReproRun.head_sha == head_sha,
            )
        )
        attempt = max((run.attempt for run in attempts), default=0) + 1
        if attempt > MAX_REPRO_RUNS:
            raise ReproLimitReached(f"head already has {MAX_REPRO_RUNS} repro runs")
        run = ReproRun(
            installation_id=ctx.installation_id,
            pr_id=pr_id,
            head_sha=head_sha,
            attempt=attempt,
            test_cmd=test_cmd,
            status=ReproStatus.QUEUED.value,
        )
        session.add(run)
        await session.flush()
        repo_full_name = await get_repo_full_name_in_transaction(session, ctx, pr.repo_id)
        await queue_repro_workflow_dispatch(
            session,
            ctx,
            run.id,
            repo_full_name,
            "heimdall-repro.yml",
            pr.number,
            run.head_sha,
            pr.base_sha,
            pr.base_ref,
            run.test_cmd,
        )
        await _enqueue_run_jobs(session, ctx, run)
        return ReproRunRef(run.id, attempt, head_sha, ReproStatus.QUEUED)


async def get_run(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    run_id: uuid.UUID,
) -> ReproRunDetails:
    """Return one reproduction attempt belonging to the requesting tenant.

    Args:
        sessions: Tenant database session factory.
        ctx: Tenant and request context.
        run_id: Reproduction attempt to retrieve.

    Returns:
        Stored attempt state and paired test results.

    Raises:
        ValueError: The attempt is not registered for this installation.
    """
    async with unit_of_work(sessions) as session:
        run = await _load_run(session, ctx, run_id)
        verdict = Verdict(run.verdict) if run.verdict is not None else None
        return ReproRunDetails(
            id=run.id,
            pr_id=run.pr_id,
            head_sha=run.head_sha,
            attempt=run.attempt,
            status=ReproStatus(run.status),
            with_fix=run.with_fix,
            without_fix=run.without_fix,
            verdict=verdict,
        )


async def record_result(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    run_id: uuid.UUID,
    with_fix: RunOutcome,
    without_fix: RunOutcome,
    *,
    review_done: bool = False,
) -> ReproResult:
    """Store paired test results, derive a verdict and complete the triage step.

    Args:
        sessions: Tenant database session factory.
        ctx: Tenant and request context.
        run_id: Reproduction attempt being completed.
        with_fix: Test result from the PR commit.
        without_fix: Test result from the base commit.
        review_done: Whether Loki already finished on this head.

    Returns:
        Saved run status and verdict.
    """
    async with unit_of_work(sessions) as session:
        run = await _load_run(session, ctx, run_id)
        if run.status == ReproStatus.FINISHED.value:
            verdict = Verdict(run.verdict) if run.verdict else None
            return ReproResult(run.id, ReproStatus.FINISHED, verdict)
        if run.status in {ReproStatus.STALE.value, ReproStatus.TIMED_OUT.value}:
            return ReproResult(run.id, ReproStatus(run.status), None)
        pr = await get_pr_snapshot(session, ctx, run.pr_id)
        if pr is None or pr.head_sha != run.head_sha:
            run.status = ReproStatus.STALE.value
            run.finished_at = datetime.now(UTC)
            return ReproResult(run.id, ReproStatus.STALE, None)
        run.with_fix = asdict(with_fix)
        run.without_fix = asdict(without_fix)
        verdict = derive_verdict(with_fix, without_fix)
        run.verdict = verdict.value
        run.status = ReproStatus.FINISHED.value
        run.finished_at = datetime.now(UTC)
        await _complete_triage_step(session, ctx, pr, run, review_done)
        return ReproResult(run.id, ReproStatus.FINISHED, verdict)


async def timeout_run(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    run_id: uuid.UUID,
    *,
    review_done: bool = False,
) -> bool:
    """Mark a queued or running attempt timed out and finish its triage step.

    Args:
        sessions: Tenant database session factory.
        ctx: Tenant and request context.
        run_id: Reproduction attempt whose timeout elapsed.
        review_done: Whether Loki already finished on this head.

    Returns:
        True if this call marked the run timed out; False if it already finished.
    """
    async with unit_of_work(sessions) as session:
        run = await _load_run(session, ctx, run_id)
        if run.status not in {ReproStatus.QUEUED.value, ReproStatus.RUNNING.value}:
            return False
        run.status = ReproStatus.TIMED_OUT.value
        run.finished_at = datetime.now(UTC)
        pr = await get_pr_snapshot(session, ctx, run.pr_id)
        if pr is not None and pr.head_sha == run.head_sha:
            await _complete_triage_step(session, ctx, pr, run, review_done)
        return True


async def _current_pr(
    session: AsyncSession,
    ctx: TenantCtx,
    pr_id: uuid.UUID,
    head_sha: str,
) -> PrSnapshot | None:
    """Read PR state only when its head matches the requested run."""
    pr = await get_pr_snapshot(session, ctx, pr_id)
    if pr is None or pr.head_sha != head_sha:
        return None
    return pr


async def _load_run(
    session: AsyncSession,
    ctx: TenantCtx,
    run_id: uuid.UUID,
) -> ReproRun:
    """Load an attempt through its tenant key."""
    result = await session.exec(
        select_for_tenant(ReproRun, ctx.installation_id)
        .where(ReproRun.id == run_id)
        .with_for_update()
    )
    run = result.first()
    if run is None:
        raise ValueError(f"reproduction run {run_id} is not registered for this installation")
    return run


async def _enqueue_run_jobs(
    session: AsyncSession,
    ctx: TenantCtx,
    run: ReproRun,
) -> None:
    """Queue a deadline job with the run and dispatch row in one commit."""
    await enqueue(
        session,
        "repro.timeout",
        {"installation_id": ctx.installation_id, "run_id": str(run.id)},
        schedule_at=datetime.now(UTC) + timedelta(seconds=REPRO_TIMEOUT_SECONDS),
        queueing_lock=f"repro-timeout:{run.id}",
    )


async def _complete_triage_step(
    session: AsyncSession,
    ctx: TenantCtx,
    pr: PrSnapshot,
    run: ReproRun,
    review_done: bool,
) -> None:
    """Report a finished or timed out run while preserving head scoping."""
    await on_step_done_in_transaction(
        session,
        ctx,
        pr.id,
        f"repro:{run.id}:finished",
        EventKind.REPRO_FINISHED,
        run.head_sha,
        Facts(repro_done=True, review_done=review_done),
    )
