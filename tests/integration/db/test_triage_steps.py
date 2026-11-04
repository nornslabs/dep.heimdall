"""Triage waits for both Huginn and Loki on the current head before the card is built."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.tenancy.api import on_installation_event
from heimdall.modules.triage.api import (
    EventKind,
    Facts,
    PrEvent,
    Transition,
    handle_event,
    on_step_done,
)
from heimdall.modules.triage.models import Pr
from heimdall.shared.context import TenantCtx
from heimdall.shared.ids import InstallationId, RepoId
from tests.integration.db.installation_helpers import created_installation

pytestmark = pytest.mark.integration

REPO = "steps-test/demo"
REPRO_DONE = (EventKind.REPRO_FINISHED, Facts(repro_done=True))
REVIEW_DONE = (EventKind.REVIEW_FINISHED, Facts(review_done=True))


async def _checking_pr(
    sessions: async_sessionmaker[AsyncSession], head_sha: str
) -> tuple[TenantCtx, RepoId, uuid.UUID]:
    """Create an enabled repo and a PR whose intake passed, so both checks are pending."""
    installation_id = int(uuid.uuid4().int % 1_000_000_000) + 3_000_000
    repo_id = RepoId(installation_id + 1)
    ctx = TenantCtx(installation_id=InstallationId(installation_id), request_id="steps-test")
    installation = created_installation(installation_id, "steps-test", repo_id, REPO)
    await on_installation_event(sessions, installation)
    await handle_event(sessions, ctx, _pr_event(repo_id, EventKind.PR_OPENED, head_sha))
    pr_id = await _pr_id(sessions, ctx)
    await _intake_passed(sessions, ctx, pr_id, head_sha)
    return ctx, repo_id, pr_id


def _pr_event(repo_id: RepoId, kind: EventKind, head_sha: str) -> PrEvent:
    """A pull request webhook event at one head."""
    return PrEvent(
        event_id=f"{kind.value}-{head_sha}",
        repo_id=repo_id,
        repo_full_name=REPO,
        number=1,
        author="octocat",
        head_sha=head_sha,
        base_sha="base-sha",
        kind=kind,
        facts=Facts(),
    )


async def _intake_passed(
    sessions: async_sessionmaker[AsyncSession], ctx: TenantCtx, pr_id: uuid.UUID, head_sha: str
) -> None:
    """Report a passing intake for one head."""
    intake = Facts(intake_passed=True)
    event_id = f"intake-{head_sha}"
    await on_step_done(sessions, ctx, pr_id, event_id, EventKind.INTAKE_DONE, head_sha, intake)


async def _pr_id(sessions: async_sessionmaker[AsyncSession], ctx: TenantCtx) -> uuid.UUID:
    """Return the only PR this test's installation owns."""
    async with unit_of_work(sessions) as session:
        return (await session.exec(select_for_tenant(Pr, ctx.installation_id))).one().id


async def _finish(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    pr_id: uuid.UUID,
    step: tuple[EventKind, Facts],
    head_sha: str,
) -> str:
    """Report one agent step finished, knowing only its own result, and return the state."""
    kind, facts = step
    event_id = f"{kind.value}-{head_sha}"
    result = await on_step_done(sessions, ctx, pr_id, event_id, kind, head_sha, facts)
    assert isinstance(result, Transition)
    return result.to.value


@pytest.mark.parametrize(
    ("first", "second"), [(REPRO_DONE, REVIEW_DONE), (REVIEW_DONE, REPRO_DONE)]
)
async def test_card_is_posted_when_the_second_check_finishes_in_either_order(
    sessions: async_sessionmaker[AsyncSession],
    first: tuple[EventKind, Facts],
    second: tuple[EventKind, Facts],
) -> None:
    """Neither agent knows about the other; triage remembers which check already finished."""
    ctx, _repo_id, pr_id = await _checking_pr(sessions, "head-1")

    assert await _finish(sessions, ctx, pr_id, first, "head-1") == "checking"
    assert await _finish(sessions, ctx, pr_id, second, "head-1") == "card_posted"


async def test_check_finished_on_an_old_head_does_not_count_after_a_push(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """A repro result for the previous commit says nothing about the new one."""
    ctx, repo_id, pr_id = await _checking_pr(sessions, "head-1")
    assert await _finish(sessions, ctx, pr_id, REPRO_DONE, "head-1") == "checking"

    await handle_event(sessions, ctx, _pr_event(repo_id, EventKind.PR_SYNCHRONIZE, "head-2"))
    await _intake_passed(sessions, ctx, pr_id, "head-2")

    assert await _finish(sessions, ctx, pr_id, REVIEW_DONE, "head-2") == "checking"
