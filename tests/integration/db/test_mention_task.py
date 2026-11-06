"""The agent.mention task answers a mention and queues exactly one reply through the outbox."""

import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import cast

import pytest
from procrastinate import App
from procrastinate.testing import InMemoryConnector
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from agents._conversation import SUBMIT_REPLY_NAME
from agents._mention import AGENT_MENTION_TASK, SENDER_ROLE_MAINTAINER
from agents._mention_ports import MentionDeps
from agents._mention_task import register_mention_task
from agents._reply import NO_PR_REPLY
from agents.loki.mention import MENTION_AGENT, guard_act_tools
from heimdall.adapters.ai import ToolCall
from heimdall.adapters.db.outbox.models import OutboxMessage
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.adapters.github.reader import GitHubApiReader
from heimdall.modules.tenancy.api import get_tenant, on_installation_event
from heimdall.modules.triage.api import EventKind, Facts, PrEvent, handle_event
from heimdall.shared.errors import AiUnavailable
from heimdall.shared.ids import InstallationId, RepoId
from tests.integration.db.installation_helpers import created_installation
from tests.unit.agents.scripted_ai import ScriptedAi, text_reply, tool_reply

pytestmark = pytest.mark.integration

MESSAGE_ID_PREFIX = "mentiontask-"
REDIRECT_TO_HUGINN = ToolCall(
    "s", SUBMIT_REPLY_NAME, {"in_scope": False, "redirect_to": "huginn", "answer": ""}
)


@pytest.fixture(autouse=True)
async def _drop_queued_replies(engine: AsyncEngine) -> AsyncIterator[None]:
    """Delete the replies these tests queue, so outbox tests never deliver them."""
    yield
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM outbox WHERE idempotency_key LIKE :pattern"),
            {"pattern": f"%:{MESSAGE_ID_PREFIX}%"},
        )


def _fresh_id() -> int:
    return uuid.uuid4().int % 1_000_000_000 + 1


async def _open_pr(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> tuple[int, str]:
    """Install a repo maintained by alice, open one PR, and return the tenant and PR ids."""
    installation_id, repo_id = _fresh_id(), _fresh_id()
    full_name = f"octo-org/r{repo_id}"
    await on_installation_event(
        sessions, created_installation(installation_id, "octo-org", repo_id, full_name)
    )
    ctx = await get_tenant(sessions, InstallationId(installation_id))
    event = PrEvent(
        event_id=f"open-{repo_id}",
        repo_id=RepoId(repo_id),
        repo_full_name=full_name,
        number=1,
        author="octocat",
        head_sha="head-1",
        base_sha="base-1",
        kind=EventKind.PR_OPENED,
        facts=Facts(),
    )
    await handle_event(sessions, ctx, event)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE repos SET config = CAST(:config AS jsonb) WHERE repo_id = :id"),
            {"config": json.dumps({"maintainers": ["alice"]}), "id": repo_id},
        )
        pr_id = (
            await conn.execute(text("SELECT id FROM prs WHERE repo_id = :id"), {"id": repo_id})
        ).scalar_one()
    return installation_id, str(pr_id)


def _loki_task(sessions: async_sessionmaker[AsyncSession], ai: ScriptedAi) -> App:
    """Register Loki's mention task on an in-memory queue app; the reader is never called."""
    queue_app = App(connector=InMemoryConnector())
    reader = cast(GitHubApiReader, object())
    deps = MentionDeps(MENTION_AGENT, sessions, reader, lambda: ai, guard_act_tools)
    register_mention_task(queue_app, deps)
    return queue_app


async def _run(queue_app: App, installation_id: int, pr_ref: str | None, message_id: str) -> None:
    # procrastinate types its registry loosely; the registered task is the coroutine function.
    registry = cast(dict[str, Callable[..., Awaitable[None]]], queue_app.tasks)  # pyright: ignore[reportUnknownMemberType]
    await registry[AGENT_MENTION_TASK](
        installation_id=installation_id,
        agent="loki",
        pr_ref=pr_ref,
        thread_id=None,
        sender="alice",
        sender_role=SENDER_ROLE_MAINTAINER,
        text="@loki does the fix reproduce?",
        message_id=message_id,
        room_id="room-1",
    )


async def _replies(
    sessions: async_sessionmaker[AsyncSession], message_id: str
) -> list[OutboxMessage]:
    async with unit_of_work(sessions) as session:
        statement = select(OutboxMessage).where(
            col(OutboxMessage.idempotency_key) == f"mention:loki:{message_id}"
        )
        return list((await session.exec(statement)).all())


async def test_a_mention_in_a_pr_thread_queues_one_reply_into_that_thread(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    installation_id, pr_id = await _open_pr(sessions, engine)
    message_id = f"{MESSAGE_ID_PREFIX}{uuid.uuid4().hex}"
    queue_app = _loki_task(sessions, ScriptedAi([tool_reply(REDIRECT_TO_HUGINN)]))

    await _run(queue_app, installation_id, pr_id, message_id)

    (row,) = await _replies(sessions, message_id)
    assert row.kind == "room.message"
    assert row.payload["channel_id"] == "room-1"
    assert row.payload["pr_id"] == pr_id
    assert "@huginn" in str(row.payload["message"])


async def test_a_redelivered_mention_does_not_queue_a_second_reply(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    installation_id, pr_id = await _open_pr(sessions, engine)
    message_id = f"{MESSAGE_ID_PREFIX}{uuid.uuid4().hex}"
    ai = ScriptedAi([tool_reply(REDIRECT_TO_HUGINN)])
    queue_app = _loki_task(sessions, ai)

    await _run(queue_app, installation_id, pr_id, message_id)
    await _run(queue_app, installation_id, pr_id, message_id)

    assert len(await _replies(sessions, message_id)) == 1
    assert len(ai.requests) == 1


async def test_a_mention_for_an_unknown_pr_is_not_threaded_under_it(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    installation_id, _pr_id = await _open_pr(sessions, engine)
    message_id = f"{MESSAGE_ID_PREFIX}{uuid.uuid4().hex}"
    queue_app = _loki_task(sessions, ScriptedAi([AiUnavailable("down")]))

    await _run(queue_app, installation_id, str(uuid.uuid4()), message_id)

    (row,) = await _replies(sessions, message_id)
    assert row.payload["message"] == f"Loki: {NO_PR_REPLY}"
    assert "pr_id" not in row.payload


async def test_a_mention_outside_a_pr_thread_gets_a_model_written_reply_without_tools(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    installation_id, _pr_id = await _open_pr(sessions, engine)
    message_id = f"{MESSAGE_ID_PREFIX}{uuid.uuid4().hex}"
    ai = ScriptedAi([text_reply("Doing well! Mention me in a PR thread and I'll take a look.")])
    queue_app = _loki_task(sessions, ai)

    await _run(queue_app, installation_id, None, message_id)

    (row,) = await _replies(sessions, message_id)
    assert (
        row.payload["message"]
        == "Loki: Doing well! Mention me in a PR thread and I'll take a look."
    )
    assert "pr_id" not in row.payload
    assert all(tools == () for _messages, tools, _choice in ai.requests)
