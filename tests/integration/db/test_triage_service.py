"""Integration tests for PR transitions and concurrent worker updates."""

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.intake.api import run_intake
from heimdall.modules.intake.models import IntakeResult
from heimdall.modules.repro.models import ReproRun
from heimdall.modules.tenancy.api import (
    InstallationEvent,
    InstallationRepo,
    get_tenant,
    on_installation_event,
)
from heimdall.modules.tenancy.models import Repo
from heimdall.modules.triage._webhooks import receive_webhook
from heimdall.modules.triage.api import (
    EventKind,
    Facts,
    PrEvent,
    Rejected,
    Transition,
    handle_event,
    handle_webhook_delivery,
    on_step_done,
)
from heimdall.modules.triage.models import Pr
from heimdall.shared.context import TenantCtx, new_request_id
from heimdall.shared.github import CiState, PullRequestInfo, RepoRef
from heimdall.shared.ids import InstallationId, RepoId

pytestmark = pytest.mark.integration


class IntakeGitHubReader:
    """Supply deterministic GitHub facts for the intake use case."""

    async def get_file(self, repo: RepoRef, path: str, ref: str) -> str | None:
        """No repo config files means use validated defaults."""
        return None

    async def get_pull_request(self, repo: RepoRef, number: int) -> PullRequestInfo:
        """Return the fixture PR at the queued commit."""
        return PullRequestInfo(
            number=number,
            title="Example change",
            body="Closes #7",
            author="octocat",
            head_sha="head-1",
            base_sha="base-sha",
            labels=(),
            assignees=(),
            state="open",
            draft=False,
        )

    async def count_open_prs_by_author(self, repo: RepoRef, author: str) -> int:
        """Return a count under the configured cap."""
        return 1

    async def get_ci_state(self, repo: RepoRef, sha: str) -> CiState:
        """Return green CI for the fixture commit."""
        return CiState.SUCCESS

    async def get_issue_assignees(self, repo: RepoRef, issue_number: int) -> tuple[str, ...]:
        """Return the author among the linked issue assignees."""
        return ("someone-else", "octocat")


async def _create_enabled_repo(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[TenantCtx, RepoId]:
    """Create one isolated installation and enabled repository."""
    installation_id = int(uuid.uuid4().int % 1_000_000_000) + 1_000_000
    repo_id = RepoId(installation_id + 1)
    await on_installation_event(
        sessions,
        InstallationEvent(
            installation_id=installation_id,
            account_login="triage-test",
            account_type="Organization",
            event="installation",
            action="created",
            repositories=(InstallationRepo(repo_id=repo_id, full_name="triage-test/demo"),),
        ),
    )
    ctx = TenantCtx(installation_id=InstallationId(installation_id), request_id="triage-test")
    return ctx, repo_id


def _pr_event(
    repo_id: RepoId,
    kind: EventKind,
    head_sha: str,
    event_id: str,
) -> PrEvent:
    """Build event input with deterministic facts for state tests."""
    return PrEvent(
        event_id=event_id,
        repo_id=repo_id,
        number=1,
        author="octocat",
        head_sha=head_sha,
        base_sha="base-sha",
        kind=kind,
        facts=Facts(intake_passed=True),
        repo_full_name="triage-test/demo",
    )


async def test_open_then_step_completion_advances_state_and_queues_intake(
    engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Commit state and queue work together, then accept the matching result."""
    ctx, repo_id = await _create_enabled_repo(sessions)
    opened = await handle_event(
        sessions, ctx, _pr_event(repo_id, EventKind.PR_OPENED, "head-1", "opened-1")
    )
    assert isinstance(opened, Transition)
    assert opened.to.value == "received"

    async with unit_of_work(sessions) as session:
        pr = (
            await session.exec(
                select_for_tenant(Pr, ctx.installation_id).where(
                    Pr.repo_id == repo_id,
                    Pr.number == 1,
                )
            )
        ).one()
        pr_id = pr.id
        assert pr.state_version == 1
        assert pr.state == "received"

    result = await on_step_done(
        sessions,
        ctx,
        pr_id,
        "intake-1",
        EventKind.INTAKE_DONE,
        "head-1",
        Facts(intake_passed=True),
    )
    assert isinstance(result, Transition)
    assert result.to.value == "checking"

    async with engine.connect() as connection:
        jobs = await connection.scalar(
            text("SELECT count(*) FROM procrastinate_jobs WHERE args->>'event_id' = 'opened-1'")
        )
    assert jobs == 1


async def test_saved_pull_request_delivery_reaches_triage_state_machine(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Consume one stored delivery and create its tenant PR state."""
    ctx, repo_id = await _create_enabled_repo(sessions)
    delivery_id = f"delivery-{uuid.uuid4().hex}"
    payload = {
        "repository": {"id": repo_id, "full_name": "triage-test/demo"},
        "installation": {"id": ctx.installation_id},
        "action": "opened",
        "pull_request": {
            "number": 1,
            "user": {"login": "octocat"},
            "head": {"sha": "head-1"},
            "base": {"sha": "base-sha"},
        },
    }
    assert await receive_webhook(sessions, delivery_id, "pull_request", "opened", payload)

    outcome = await handle_webhook_delivery(sessions, ctx, delivery_id)

    assert isinstance(outcome, Transition)
    async with unit_of_work(sessions) as session:
        pr = (
            await session.exec(
                select_for_tenant(Pr, ctx.installation_id).where(
                    Pr.repo_id == repo_id,
                    Pr.number == 1,
                )
            )
        ).one()
        assert pr.state == "received"


async def test_installation_delivery_routes_to_tenancy_before_tenant_exists(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Process installation creation without requiring a preexisting tenant row."""
    installation_id = InstallationId(int(uuid.uuid4().int % 1_000_000_000) + 1_000_000)
    ctx = TenantCtx(installation_id=installation_id, request_id=new_request_id())
    delivery_id = f"install-{uuid.uuid4().hex}"
    payload = {
        "action": "created",
        "installation": {
            "id": installation_id,
            "account": {"login": "new-org", "type": "Organization"},
        },
        "repositories": [],
    }
    assert await receive_webhook(sessions, delivery_id, "installation", "created", payload)

    outcome = await handle_webhook_delivery(sessions, ctx, delivery_id)

    assert outcome is None
    assert (await get_tenant(sessions, installation_id)).installation_id == installation_id


async def test_run_intake_saves_checklist_and_switch_outbox_atomically(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Save six checks, transition the PR and queue its Switch checklist together."""
    ctx, repo_id = await _create_enabled_repo(sessions)
    await handle_event(
        sessions,
        ctx,
        _pr_event(repo_id, EventKind.PR_OPENED, "head-1", "open-intake"),
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
        pr_id = pr.id
        repo = (
            await session.exec(
                select_for_tenant(Repo, ctx.installation_id).where(Repo.repo_id == repo_id)
            )
        ).one()
        repo.switch_room_id = "switch-room-1"

    repo_ref = RepoRef(installation_id=ctx.installation_id, full_name="triage-test/demo")
    results = await run_intake(
        sessions,
        IntakeGitHubReader(),
        ctx,
        repo_ref,
        repo_id,
        pr_id,
        1,
        "head-1",
        "intake-delivery-1",
    )

    assert len(results) == 6
    assert all(result.passed for result in results)
    async with unit_of_work(sessions) as session:
        pr = (
            await session.exec(select_for_tenant(Pr, ctx.installation_id).where(Pr.id == pr_id))
        ).one()
        saved_results = await session.exec(
            select_for_tenant(IntakeResult, ctx.installation_id).where(
                IntakeResult.pr_id == pr_id,
                IntakeResult.head_sha == "head-1",
            )
        )
        connection = await session.connection()
        outbox = await connection.execute(
            text("SELECT payload FROM outbox WHERE idempotency_key = :key"),
            {"key": "triage:intake-delivery-1:outbox_thread_and_checklist"},
        )
        assert pr.state == "checking"
        assert len(saved_results.all()) == 6
        assert "Intake checklist" in outbox.scalar_one()["message"]


async def test_intake_failure_publishes_visible_error_to_switch(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Show failed intake rules in the PR's Switch room through the outbox."""
    ctx, repo_id = await _create_enabled_repo(sessions)
    await handle_event(
        sessions,
        ctx,
        _pr_event(repo_id, EventKind.PR_OPENED, "head-1", "open-failed-intake"),
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
        pr_id = pr.id

    result = await on_step_done(
        sessions,
        ctx,
        pr_id,
        "failed-intake-result",
        EventKind.INTAKE_DONE,
        "head-1",
        Facts(intake_passed=False),
        room_id="switch-room-1",
        room_message="Intake failed: required label is missing.",
    )

    assert isinstance(result, Transition)
    assert result.to.value == "intake_failed"
    async with unit_of_work(sessions) as session:
        connection = await session.connection()
        outbox = await connection.execute(
            text("SELECT payload FROM outbox WHERE idempotency_key = :key"),
            {"key": "triage:failed-intake-result:outbox_failed_rules"},
        )
        message = outbox.scalar_one()["message"]
        assert "Intake failed" in message
        assert "required label is missing" in message


async def test_stale_step_result_does_not_change_pr_state(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Ignore an intake result for a commit superseded by synchronization."""
    ctx, repo_id = await _create_enabled_repo(sessions)
    await handle_event(
        sessions,
        ctx,
        _pr_event(repo_id, EventKind.PR_OPENED, "head-1", "open-stale"),
    )
    await handle_event(
        sessions,
        ctx,
        _pr_event(repo_id, EventKind.PR_SYNCHRONIZE, "head-2", "sync-stale"),
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
        pr_id = pr.id
    result = await on_step_done(
        sessions,
        ctx,
        pr_id,
        "intake-stale",
        EventKind.INTAKE_DONE,
        "head-1",
        Facts(intake_passed=True),
    )
    assert isinstance(result, Rejected)
    assert result.reason.value == "stale_head"


async def test_synchronization_marks_prior_intake_and_repro_results_stale(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Persist stale markers alongside the new PR head and queued intake job."""
    ctx, repo_id = await _create_enabled_repo(sessions)
    await handle_event(
        sessions,
        ctx,
        _pr_event(repo_id, EventKind.PR_OPENED, "head-1", "open-old-results"),
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
        pr_id = pr.id
        session.add(
            IntakeResult(
                installation_id=ctx.installation_id,
                pr_id=pr_id,
                head_sha="head-1",
                rule="ci_green",
                passed=True,
            )
        )
        session.add(
            ReproRun(
                installation_id=ctx.installation_id,
                pr_id=pr_id,
                head_sha="head-1",
                attempt=1,
                test_cmd="pytest",
                status="finished",
            )
        )

    await handle_event(
        sessions,
        ctx,
        _pr_event(repo_id, EventKind.PR_SYNCHRONIZE, "head-2", "sync-old-results"),
    )

    async with unit_of_work(sessions) as session:
        intake = (
            await session.exec(
                select_for_tenant(IntakeResult, ctx.installation_id).where(
                    IntakeResult.pr_id == pr_id
                )
            )
        ).one()
        repro = (
            await session.exec(
                select_for_tenant(ReproRun, ctx.installation_id).where(ReproRun.pr_id == pr_id)
            )
        ).one()
        assert intake.stale
        assert repro.status == "stale"


async def test_concurrent_synchronizations_keep_a_valid_version(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Concurrent workers cannot overwrite a PR without matching its version."""
    ctx, repo_id = await _create_enabled_repo(sessions)
    await handle_event(
        sessions,
        ctx,
        _pr_event(repo_id, EventKind.PR_OPENED, "head-1", "open-race"),
    )
    first, second = await asyncio.gather(
        handle_event(
            sessions,
            ctx,
            _pr_event(repo_id, EventKind.PR_SYNCHRONIZE, "head-2", "sync-a"),
        ),
        handle_event(
            sessions,
            ctx,
            _pr_event(repo_id, EventKind.PR_SYNCHRONIZE, "head-3", "sync-b"),
        ),
    )

    assert isinstance(first, (Transition, Rejected))
    assert isinstance(second, (Transition, Rejected))
    async with unit_of_work(sessions) as session:
        pr = (
            await session.exec(
                select_for_tenant(Pr, ctx.installation_id).where(
                    Pr.repo_id == repo_id,
                    Pr.number == 1,
                )
            )
        ).one()
        assert pr.state_version in {2, 3}
        assert pr.head_sha in {"head-2", "head-3"}
