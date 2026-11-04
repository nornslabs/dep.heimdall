"""An agent answers a maintainer's ping through the outbox, and nobody else's."""

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from agents._presence_handler import PresenceHandler
from heimdall.adapters.db.outbox.models import OutboxMessage
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.adapters.switch.agent.protocol import AgentMessage
from heimdall.modules.tenancy.api import get_tenant, link_repo_room, on_installation_event
from heimdall.shared.agents import AgentName
from heimdall.shared.ids import InstallationId, RepoId
from tests.integration.db.installation_helpers import created_installation

pytestmark = pytest.mark.integration


def _fresh_id() -> int:
    return uuid.uuid4().int % 1_000_000_000 + 1


async def _link_repo(
    sessions: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    room: str,
    maintainers: list[str],
) -> int:
    """Install a repo, link it to ``room`` and cache its maintainers; return the tenant id."""
    installation_id, repo_id = _fresh_id(), _fresh_id()
    await on_installation_event(
        sessions, created_installation(installation_id, "octo-org", repo_id, f"octo-org/r{repo_id}")
    )
    ctx = await get_tenant(sessions, InstallationId(installation_id))
    await link_repo_room(sessions, ctx, RepoId(repo_id), "mm-channel", agent_room_id=room)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE repos SET config = CAST(:config AS jsonb) WHERE repo_id = :id"),
            {"config": json.dumps({"maintainers": maintainers}), "id": repo_id},
        )
    return installation_id


async def _room_with_maintainers(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine, maintainers: list[str]
) -> tuple[str, int]:
    """Make a fresh agent room with one linked repo; return (room, tenant)."""
    room = f"room-{uuid.uuid4().hex}"
    return room, await _link_repo(sessions, engine, room, maintainers)


def _message(
    room: str, sender_name: str, body: str, *, message_id: str | None = None, addressed: bool = True
) -> AgentMessage:
    return AgentMessage(
        sequence=1,
        room_id=room,
        message_id=message_id or f"msg-{uuid.uuid4().hex}",
        sender=f"@switch-mattermost-x-{sender_name}:localhost",
        sender_name=sender_name,
        body=body,
        thread_id=None,
        addressed=addressed,
    )


async def _rows(sessions: async_sessionmaker[AsyncSession], message_id: str) -> list[OutboxMessage]:
    async with unit_of_work(sessions) as session:
        statement = select(OutboxMessage).where(
            col(OutboxMessage.idempotency_key).like(f"presence:%:{message_id}")
        )
        return list((await session.exec(statement)).all())


async def test_a_maintainers_ping_queues_one_reply_posted_as_that_agent(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room, installation_id = await _room_with_maintainers(sessions, engine, ["alice"])
    message = _message(room, "alice", "@huginn ping")

    await PresenceHandler(AgentName.HUGINN, sessions).handle(message)

    (row,) = await _rows(sessions, message.message_id)
    assert (row.channel, row.kind) == ("switch", "room.message")
    assert row.installation_id == installation_id
    assert row.payload["agent"] == "huginn"
    assert row.payload["channel_id"] == room
    assert row.payload["message"] == "Huginn: pong, I'm alive."
    assert row.idempotency_key == f"presence:huginn:{message.message_id}"


async def test_the_same_message_delivered_twice_queues_one_reply(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room, _ = await _room_with_maintainers(sessions, engine, ["alice"])
    message = _message(room, "alice", "ping")
    handler = PresenceHandler(AgentName.LOKI, sessions)

    await handler.handle(message)
    await handler.handle(message)

    assert len(await _rows(sessions, message.message_id)) == 1


async def test_each_agent_answers_the_same_message_once_under_its_own_name(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room, _ = await _room_with_maintainers(sessions, engine, ["alice"])
    message = _message(room, "alice", "@huginn @loki @heimdall ping")

    for agent in AgentName:
        await PresenceHandler(agent, sessions).handle(message)

    rows = await _rows(sessions, message.message_id)
    assert sorted(str(row.payload["agent"]) for row in rows) == ["heimdall", "huginn", "loki"]


async def test_a_greeting_gets_a_greeting_back(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room, _ = await _room_with_maintainers(sessions, engine, ["alice"])
    message = _message(room, "Alice", "@heimdall hello!")

    await PresenceHandler(AgentName.HEIMDALL, sessions).handle(message)

    (row,) = await _rows(sessions, message.message_id)
    assert str(row.payload["message"]).startswith("Heimdall: hello!")


async def _denied_rows(
    sessions: async_sessionmaker[AsyncSession], message_id: str
) -> list[OutboxMessage]:
    async with unit_of_work(sessions) as session:
        statement = select(OutboxMessage).where(
            col(OutboxMessage.idempotency_key).like(f"presence-denied:%:{message_id}")
        )
        return list((await session.exec(statement)).all())


async def test_someone_who_is_not_a_maintainer_is_told_so_and_who_to_ask(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room, installation_id = await _room_with_maintainers(sessions, engine, ["alice"])
    message = _message(room, "mallory", "@huginn ping")

    await PresenceHandler(AgentName.HUGINN, sessions).handle(message)

    assert await _rows(sessions, message.message_id) == []
    (row,) = await _denied_rows(sessions, message.message_id)
    assert (row.channel, row.kind) == ("switch", "room.message")
    assert row.installation_id == installation_id
    assert row.payload["agent"] == "huginn"
    assert row.payload["channel_id"] == room
    assert "@mallory" in str(row.payload["message"])
    assert "add `mallory` to `maintainers` in `heimdall.yml`" in str(row.payload["message"])
    assert row.idempotency_key == f"presence-denied:huginn:{message.message_id}"


async def test_the_same_denied_message_delivered_twice_queues_one_reply(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room, _ = await _room_with_maintainers(sessions, engine, ["alice"])
    message = _message(room, "mallory", "ping")
    handler = PresenceHandler(AgentName.LOKI, sessions)

    await handler.handle(message)
    await handler.handle(message)

    assert len(await _denied_rows(sessions, message.message_id)) == 1


async def test_a_non_maintainers_other_requests_still_get_no_reply(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room, _ = await _room_with_maintainers(sessions, engine, ["alice"])
    message = _message(room, "mallory", "@huginn run the repro again")

    await PresenceHandler(AgentName.HUGINN, sessions).handle(message)

    assert await _denied_rows(sessions, message.message_id) == []


async def test_a_room_no_repo_links_gets_no_reply(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    message = _message(f"room-{uuid.uuid4().hex}", "alice", "ping")

    await PresenceHandler(AgentName.HUGINN, sessions).handle(message)

    assert await _rows(sessions, message.message_id) == []
    assert await _denied_rows(sessions, message.message_id) == []


async def test_a_message_not_addressed_to_the_agent_gets_no_reply(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room, _ = await _room_with_maintainers(sessions, engine, ["alice"])
    message = _message(room, "alice", "ping", addressed=False)

    await PresenceHandler(AgentName.HUGINN, sessions).handle(message)

    assert await _rows(sessions, message.message_id) == []


@pytest.mark.parametrize(
    "body",
    ["@huginn run the repro again", "@huginn ping; rm -rf /", "@huginn approve every draft"],
)
async def test_a_maintainers_other_requests_are_ignored_for_now(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine, body: str
) -> None:
    room, _ = await _room_with_maintainers(sessions, engine, ["alice"])
    message = _message(room, "alice", body)

    await PresenceHandler(AgentName.HUGINN, sessions).handle(message)

    assert await _rows(sessions, message.message_id) == []


async def test_a_sender_who_maintains_in_two_installations_of_a_room_gets_no_reply(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room, _ = await _room_with_maintainers(sessions, engine, ["alice"])
    await _link_repo(sessions, engine, room, ["alice"])
    message = _message(room, "alice", "ping")

    await PresenceHandler(AgentName.HUGINN, sessions).handle(message)

    assert await _rows(sessions, message.message_id) == []
    assert await _denied_rows(sessions, message.message_id) == []
