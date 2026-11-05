"""Question limit against real Postgres: counting per scope, one notice, redelivery, tenancy."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.conversation.api import (
    MAX_QUESTIONS_PER_WINDOW,
    QUESTION_WINDOW,
    Admitted,
    Limited,
    QuestionDecision,
    QuestionScope,
    admit_question_in_transaction,
)
from heimdall.modules.tenancy.api import on_installation_event
from heimdall.shared.agents import AgentName
from heimdall.shared.context import TenantCtx
from heimdall.shared.ids import InstallationId
from tests.integration.db.installation_helpers import created_installation

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


def _fresh_id() -> int:
    return uuid.uuid4().int % 1_000_000_000 + 1


def _ctx() -> TenantCtx:
    return TenantCtx(installation_id=InstallationId(_fresh_id()), request_id="conversation-test")


def _scope(
    *,
    agent: AgentName = AgentName.LOKI,
    sender: str = "alice",
    room_id: str = "room-1",
    pr_id: uuid.UUID | None = None,
) -> QuestionScope:
    return QuestionScope(agent=agent, sender=sender, pr_id=pr_id, room_id=room_id)


async def _ask(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    scope: QuestionScope,
    message_id: str,
    at: datetime = NOW,
) -> QuestionDecision:
    async with unit_of_work(sessions) as session:
        return await admit_question_in_transaction(session, ctx, scope, message_id, at)


async def _fill(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    scope: QuestionScope,
    *,
    count: int = MAX_QUESTIONS_PER_WINDOW,
    at: datetime = NOW,
) -> None:
    for index in range(count):
        decision = await _ask(sessions, ctx, scope, f"fill-{uuid.uuid4()}-{index}", at)
        assert decision == Admitted()


async def test_question_past_the_limit_gets_one_notice_then_silence(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ctx, scope = _ctx(), _scope()
    await _fill(sessions, ctx, scope)

    first = await _ask(sessions, ctx, scope, "over-1")
    second = await _ask(sessions, ctx, scope, "over-2")
    third = await _ask(sessions, ctx, scope, "over-3")

    assert first == Limited(retry_after=NOW + QUESTION_WINDOW, send_notice=True)
    assert second == Limited(retry_after=NOW + QUESTION_WINDOW, send_notice=False)
    assert third == Limited(retry_after=NOW + QUESTION_WINDOW, send_notice=False)


async def test_a_redelivered_message_is_not_counted_twice(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ctx, scope = _ctx(), _scope()
    await _ask(sessions, ctx, scope, "dup")
    assert await _ask(sessions, ctx, scope, "dup") == Admitted()

    await _fill(sessions, ctx, scope, count=MAX_QUESTIONS_PER_WINDOW - 1)

    assert isinstance(await _ask(sessions, ctx, scope, "one-too-many"), Limited)


async def test_a_redelivered_notice_message_is_refused_without_a_second_notice(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ctx, scope = _ctx(), _scope()
    await _fill(sessions, ctx, scope)
    first = await _ask(sessions, ctx, scope, "over-1")

    again = await _ask(sessions, ctx, scope, "over-1")

    assert isinstance(first, Limited)
    assert first.send_notice is True
    assert isinstance(again, Limited)
    assert again.send_notice is False
    assert again.retry_after == first.retry_after


async def test_each_sender_agent_and_room_has_its_own_allowance(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ctx, scope = _ctx(), _scope()
    await _fill(sessions, ctx, scope)

    others = [
        _scope(sender="bob"),
        _scope(agent=AgentName.HUGINN),
        _scope(room_id="room-2"),
    ]

    for other in others:
        assert await _ask(sessions, ctx, other, f"other-{uuid.uuid4()}") == Admitted()


async def test_sender_names_match_ignoring_case(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ctx = _ctx()
    await _fill(sessions, ctx, _scope(sender="Alice"))

    decision = await _ask(sessions, ctx, _scope(sender="alice"), "lowercase")

    assert isinstance(decision, Limited)


async def test_each_pr_has_its_own_allowance(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    ctx = _ctx()
    first_pr, second_pr = await _two_prs(sessions, engine, ctx)
    await _fill(sessions, ctx, _scope(pr_id=first_pr))

    over = await _ask(sessions, ctx, _scope(pr_id=first_pr), "first-pr-over")
    fresh = await _ask(sessions, ctx, _scope(pr_id=second_pr), "second-pr-first")

    assert isinstance(over, Limited)
    assert fresh == Admitted()


async def test_tenants_do_not_share_counts(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ctx, other_ctx, scope = _ctx(), _ctx(), _scope()
    await _fill(sessions, ctx, scope)

    assert await _ask(sessions, other_ctx, scope, "other-tenant") == Admitted()


async def test_the_allowance_returns_once_the_window_has_passed(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ctx, scope = _ctx(), _scope()
    await _fill(sessions, ctx, scope)

    later = NOW + QUESTION_WINDOW + timedelta(seconds=1)

    assert await _ask(sessions, ctx, scope, "after-window", later) == Admitted()


async def test_a_new_episode_of_limiting_gets_its_own_notice(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ctx, scope = _ctx(), _scope()
    await _fill(sessions, ctx, scope)
    await _ask(sessions, ctx, scope, "episode-1")
    later = NOW + QUESTION_WINDOW + timedelta(seconds=1)
    await _fill(sessions, ctx, scope, at=later)

    decision = await _ask(sessions, ctx, scope, "episode-2", later)

    assert isinstance(decision, Limited)
    assert decision.send_notice is True


async def _two_prs(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine, ctx: TenantCtx
) -> tuple[uuid.UUID, uuid.UUID]:
    """Create ``ctx``'s installation with one repo and two PRs, and return the PR ids."""
    repo_id = _fresh_id()
    await on_installation_event(
        sessions,
        created_installation(ctx.installation_id, "octo-org", repo_id, f"octo-org/r{repo_id}"),
    )
    first, second = uuid.uuid4(), uuid.uuid4()
    async with engine.begin() as conn:
        for number, pr_id in enumerate((first, second), start=1):
            await conn.execute(
                text(
                    "INSERT INTO prs (installation_id, id, repo_id, number, author, head_sha, "
                    "base_sha, state) VALUES (:installation_id, :id, :repo_id, :number, "
                    "'carol', 'h', 'b', 'new')"
                ),
                {
                    "installation_id": ctx.installation_id,
                    "id": pr_id,
                    "repo_id": repo_id,
                    "number": number,
                },
            )
    return first, second
