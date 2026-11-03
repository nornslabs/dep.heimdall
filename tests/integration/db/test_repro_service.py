"""Integration coverage for reproduction attempts and their queue jobs."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.outbox.models import OutboxMessage
from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.approval.workflow_models import WorkflowDispatchApproval
from heimdall.modules.repro.api import (
    MAX_REPRO_RUNS,
    ReproStatus,
    RunOutcome,
    Verdict,
    get_run,
    record_result,
    start_run,
    timeout_run,
)
from heimdall.modules.repro.models import ReproRun
from heimdall.modules.tenancy.api import on_installation_event
from heimdall.modules.triage.api import EventKind, Facts, PrEvent, handle_event, on_step_done
from heimdall.modules.triage.models import Pr
from heimdall.shared.context import TenantCtx
from heimdall.shared.errors import ReproLimitReached
from heimdall.shared.ids import InstallationId, RepoId
from tests.integration.db.installation_helpers import created_installation

pytestmark = pytest.mark.integration


async def _checking_pr(sessions: async_sessionmaker[AsyncSession]) -> tuple[TenantCtx, uuid.UUID]:
    """Create an enabled repository and a PR ready for repro work."""
    installation_id = int(uuid.uuid4().int % 1_000_000_000) + 1_000_000
    repo_id = RepoId(installation_id + 1)
    ctx = TenantCtx(installation_id=InstallationId(installation_id), request_id="repro-test")
    await on_installation_event(
        sessions,
        created_installation(installation_id, "repro-test", repo_id, "repro-test/demo"),
    )
    await handle_event(
        sessions,
        ctx,
        PrEvent(
            event_id="open-repro",
            repo_id=repo_id,
            repo_full_name="repro-test/demo",
            number=1,
            author="octocat",
            head_sha="head-1",
            base_sha="base-1",
            base_ref="main",
            kind=EventKind.PR_OPENED,
            facts=Facts(),
        ),
    )
    async with unit_of_work(sessions) as session:
        pr = (
            await session.exec(
                select_for_tenant(Pr, ctx.installation_id).where(
                    Pr.repo_id == repo_id,
                    Pr.number == 1,
                )
            )
        ).one()
        assert pr.base_ref == "main"
        pr_id = pr.id
    await on_step_done(
        sessions,
        ctx,
        pr_id,
        "intake-repro",
        EventKind.INTAKE_DONE,
        "head-1",
        Facts(intake_passed=True),
    )
    return ctx, pr_id


async def test_start_run_queues_execution_and_timeout_together(
    engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Persist a queued attempt, authorized dispatch, and deadline job together."""
    ctx, pr_id = await _checking_pr(sessions)
    run = await start_run(sessions, ctx, pr_id, "head-1", "pytest -q")

    assert run.attempt == 1
    assert run.status is ReproStatus.QUEUED
    async with engine.connect() as connection:
        jobs = await connection.scalar(
            text("SELECT count(*) FROM procrastinate_jobs WHERE args->>'run_id' = :run_id"),
            {"run_id": str(run.id)},
        )
    assert jobs == 1
    async with unit_of_work(sessions) as session:
        dispatch = (
            await session.exec(
                select_for_tenant(OutboxMessage, ctx.installation_id).where(
                    OutboxMessage.kind == "github.workflow_dispatch"
                )
            )
        ).one()
        approval = (
            await session.exec(
                select_for_tenant(WorkflowDispatchApproval, ctx.installation_id).where(
                    WorkflowDispatchApproval.repro_run_id == run.id
                )
            )
        ).one()
        assert dispatch.payload["approval_id"] == str(approval.id)
        assert dispatch.payload["request_hash"] == approval.request_hash
        assert dispatch.payload["ref"] == "main"
        assert dispatch.payload["inputs"]["base_sha"] == "base-1"
        assert approval.approver == "policy:repro_dispatch"


async def test_get_run_returns_attempt_for_its_installation(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Read queued run state through a tenant-scoped use case."""
    ctx, pr_id = await _checking_pr(sessions)
    run = await start_run(sessions, ctx, pr_id, "head-1", "pytest -q")

    details = await get_run(sessions, ctx, run.id)

    assert details.id == run.id
    assert details.pr_id == pr_id
    assert details.status is ReproStatus.QUEUED
    other_tenant = TenantCtx(
        installation_id=InstallationId(ctx.installation_id + 1), request_id="other-tenant"
    )
    with pytest.raises(ValueError):
        await get_run(sessions, other_tenant, run.id)


async def test_record_result_derives_verdict_and_finishes_run(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Save both outcomes and complete the run only for its matching PR head."""
    ctx, pr_id = await _checking_pr(sessions)
    run = await start_run(sessions, ctx, pr_id, "head-1", "pytest -q")

    result = await record_result(
        sessions,
        ctx,
        run.id,
        RunOutcome(passed=True, duration_seconds=5.0),
        RunOutcome(passed=False, failures=("test_example",), duration_seconds=3.0),
    )

    assert result.status is ReproStatus.FINISHED
    assert result.verdict is Verdict.FIX_CONFIRMED
    async with unit_of_work(sessions) as session:
        saved = (
            await session.exec(
                select_for_tenant(ReproRun, ctx.installation_id).where(ReproRun.id == run.id)
            )
        ).one()
        assert saved.with_fix == {"passed": True, "failures": [], "duration_seconds": 5.0}
        assert saved.without_fix == {
            "passed": False,
            "failures": ["test_example"],
            "duration_seconds": 3.0,
        }
        assert saved.verdict == Verdict.FIX_CONFIRMED.value


async def test_timeout_is_idempotent_and_finishes_the_run(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """A timeout changes an active run once and ignores later timeout deliveries."""
    ctx, pr_id = await _checking_pr(sessions)
    run = await start_run(sessions, ctx, pr_id, "head-1", "pytest -q")

    assert await timeout_run(sessions, ctx, run.id)
    assert not await timeout_run(sessions, ctx, run.id)
    async with unit_of_work(sessions) as session:
        saved = (
            await session.exec(
                select_for_tenant(ReproRun, ctx.installation_id).where(ReproRun.id == run.id)
            )
        ).one()
    assert saved.status == ReproStatus.TIMED_OUT.value


async def test_start_run_enforces_the_attempt_limit_per_head(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Stop queuing attempts after the configured hard limit."""
    ctx, pr_id = await _checking_pr(sessions)
    for _ in range(MAX_REPRO_RUNS):
        await start_run(sessions, ctx, pr_id, "head-1", "pytest -q")

    with pytest.raises(ReproLimitReached):
        await start_run(sessions, ctx, pr_id, "head-1", "pytest -q")
