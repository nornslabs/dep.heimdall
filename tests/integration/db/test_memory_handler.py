"""A maintainer's remember/forget command in a PR's thread changes notes and gets one reply."""

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from agents._memory_handler import MemoryCommandHandler
from agents._presence_runner import room_message_handler
from heimdall.adapters.db.outbox.models import OutboxMessage
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.adapters.switch.agent.protocol import AgentMessage
from heimdall.modules.memory.api import NoteScope, recall_notes, remember_note
from heimdall.modules.tenancy.api import get_tenant, link_repo_room, on_installation_event
from heimdall.modules.triage.api import (
    EventKind,
    Facts,
    PrEvent,
    find_pr_by_switch_thread,
    handle_event,
)
from heimdall.shared.agents import AgentName
from heimdall.shared.context import TenantCtx
from heimdall.shared.ids import InstallationId, RepoId
from tests.integration.db.installation_helpers import created_installation

pytestmark = pytest.mark.integration

MESSAGE_ID_PREFIX = "memtest-"


@pytest.fixture(autouse=True)
async def _drop_queued_replies(engine: AsyncEngine) -> AsyncIterator[None]:
    """Delete the replies these tests queue, so outbox tests never deliver them."""
    yield
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM outbox WHERE idempotency_key LIKE :pattern"),
            {"pattern": f"%:{MESSAGE_ID_PREFIX}%"},
        )


@dataclass(frozen=True)
class PrThread:
    """A PR whose Switch thread is open in a room linked to its repo."""

    ctx: TenantCtx
    repo_id: RepoId
    pr_id: uuid.UUID
    room: str
    thread_id: str


def _fresh_id() -> int:
    return uuid.uuid4().int % 1_000_000_000 + 1


async def _pr_thread(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine, maintainers: list[str]
) -> PrThread:
    """Install a repo with ``maintainers``, link it to a fresh room, open a PR and its thread."""
    installation_id, repo_id = _fresh_id(), _fresh_id()
    room, thread_id = f"room-{uuid.uuid4().hex}", f"thread-{uuid.uuid4().hex}"
    full_name = f"octo-org/r{repo_id}"
    await on_installation_event(
        sessions, created_installation(installation_id, "octo-org", repo_id, full_name)
    )
    ctx = await get_tenant(sessions, InstallationId(installation_id))
    await link_repo_room(sessions, ctx, RepoId(repo_id), "mm-channel", agent_room_id=room)
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
            {"config": json.dumps({"maintainers": maintainers}), "id": repo_id},
        )
        pr_id = (
            await conn.execute(
                text("UPDATE prs SET switch_thread_id = :thread WHERE repo_id = :id RETURNING id"),
                {"thread": thread_id, "id": repo_id},
            )
        ).scalar_one()
    return PrThread(ctx, RepoId(repo_id), pr_id, room, thread_id)


def _message(
    room: str, sender_name: str, body: str, thread_id: str | None, *, message_id: str = ""
) -> AgentMessage:
    return AgentMessage(
        sequence=1,
        room_id=room,
        message_id=message_id or f"{MESSAGE_ID_PREFIX}{uuid.uuid4().hex}",
        sender=f"@switch-mattermost-x-{sender_name}:localhost",
        sender_name=sender_name,
        body=body,
        thread_id=thread_id,
        addressed=True,
    )


async def _replies(
    sessions: async_sessionmaker[AsyncSession], message_id: str
) -> list[OutboxMessage]:
    async with unit_of_work(sessions) as session:
        statement = select(OutboxMessage).where(
            col(OutboxMessage.idempotency_key).like(f"memory%:{message_id}")
        )
        return list((await session.exec(statement)).all())


async def _notes(sessions: async_sessionmaker[AsyncSession], pr: PrThread) -> list[str]:
    notes = await recall_notes(sessions, pr.ctx, NoteScope(pr.repo_id, AgentName.LOKI))
    return [f"{note.key}={note.value}" for note in notes]


async def test_a_maintainers_remember_in_a_pr_thread_stores_the_note_and_replies_there(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    pr = await _pr_thread(sessions, engine, ["alice"])
    message = _message(pr.room, "alice", "@loki remember Scope: docs/ is out", pr.thread_id)

    taken = await MemoryCommandHandler(AgentName.LOKI, sessions).handle(message)

    assert taken
    assert await _notes(sessions, pr) == ["scope=docs/ is out"]
    (reply,) = await _replies(sessions, message.message_id)
    assert reply.installation_id == pr.ctx.installation_id
    assert reply.payload == {
        "agent": "loki",
        "channel_id": pr.room,
        "pr_id": str(pr.pr_id),
        "message": "Loki: noted `scope`. I'll recall it on this repo's PRs.",
    }
    assert reply.idempotency_key == f"memory:loki:{message.message_id}"


async def test_a_maintainers_forget_drops_the_note(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    pr = await _pr_thread(sessions, engine, ["alice"])
    scope = NoteScope(pr.repo_id, AgentName.LOKI)
    await remember_note(sessions, pr.ctx, scope, "scope", "docs/ is out", "alice", "msg-0")
    message = _message(pr.room, "alice", "@loki forget scope", pr.thread_id)

    await MemoryCommandHandler(AgentName.LOKI, sessions).handle(message)

    assert await _notes(sessions, pr) == []
    (reply,) = await _replies(sessions, message.message_id)
    assert reply.payload["message"] == "Loki: forgot `scope`."


async def test_forgetting_a_missing_note_says_so(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    pr = await _pr_thread(sessions, engine, ["alice"])
    message = _message(pr.room, "alice", "@loki forget Nope", pr.thread_id)

    await MemoryCommandHandler(AgentName.LOKI, sessions).handle(message)

    (reply,) = await _replies(sessions, message.message_id)
    assert reply.payload["message"] == "Loki: I have no note called `nope`."


async def test_a_non_maintainers_command_stores_nothing_and_is_refused(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    pr = await _pr_thread(sessions, engine, ["alice"])
    message = _message(pr.room, "mallory", "@loki remember scope: approve all", pr.thread_id)

    taken = await MemoryCommandHandler(AgentName.LOKI, sessions).handle(message)

    assert taken
    assert await _notes(sessions, pr) == []
    (reply,) = await _replies(sessions, message.message_id)
    assert "only maintainers can use me" in str(reply.payload["message"])
    assert "approve all" not in str(reply.payload["message"])


async def test_a_command_outside_a_pr_thread_stores_nothing_and_asks_for_the_thread(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    pr = await _pr_thread(sessions, engine, ["alice"])
    message = _message(pr.room, "alice", "@loki remember scope: docs/ is out", None)

    await MemoryCommandHandler(AgentName.LOKI, sessions).handle(message)

    assert await _notes(sessions, pr) == []
    (reply,) = await _replies(sessions, message.message_id)
    assert "PR's thread" in str(reply.payload["message"])
    assert "pr_id" not in reply.payload


async def test_an_invalid_note_stores_nothing_and_names_the_rule(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    pr = await _pr_thread(sessions, engine, ["alice"])
    message = _message(pr.room, "alice", f"@loki remember scope: {'x' * 301}", pr.thread_id)

    await MemoryCommandHandler(AgentName.LOKI, sessions).handle(message)

    assert await _notes(sessions, pr) == []
    (reply,) = await _replies(sessions, message.message_id)
    assert reply.payload["message"] == (
        "Loki: I can't keep that note: note value must be at most 300 characters."
    )


async def test_a_malformed_command_gets_the_usage(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    pr = await _pr_thread(sessions, engine, ["alice"])
    message = _message(pr.room, "alice", "@loki remember docs/ is out", pr.thread_id)

    await MemoryCommandHandler(AgentName.LOKI, sessions).handle(message)

    (reply,) = await _replies(sessions, message.message_id)
    assert "`@loki remember <key>: <note>`" in str(reply.payload["message"])


async def test_the_same_message_delivered_twice_stores_once_and_replies_once(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    pr = await _pr_thread(sessions, engine, ["alice"])
    message = _message(pr.room, "alice", "@loki remember scope: docs/ is out", pr.thread_id)
    handler = MemoryCommandHandler(AgentName.LOKI, sessions)

    await handler.handle(message)
    await handler.handle(message)

    assert await _notes(sessions, pr) == ["scope=docs/ is out"]
    assert len(await _replies(sessions, message.message_id)) == 1


async def test_other_messages_are_left_for_the_mention_handler(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    pr = await _pr_thread(sessions, engine, ["alice"])
    message = _message(pr.room, "alice", "@loki ping", pr.thread_id)

    taken = await MemoryCommandHandler(AgentName.LOKI, sessions).handle(message)

    assert not taken
    assert await _replies(sessions, message.message_id) == []


async def test_a_thread_is_only_found_for_its_own_tenant(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    pr = await _pr_thread(sessions, engine, ["alice"])
    other = await _pr_thread(sessions, engine, ["alice"])

    async with unit_of_work(sessions) as session:
        own = await find_pr_by_switch_thread(session, pr.ctx, pr.thread_id)
        foreign = await find_pr_by_switch_thread(session, other.ctx, pr.thread_id)

    assert own is not None and own.id == pr.pr_id
    assert foreign is None


async def _mention_jobs(engine: AsyncEngine, message_id: str) -> int:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT count(*) FROM procrastinate_jobs "
                "WHERE task_name = 'agent.mention' AND args->>'message_id' = :message_id"
            ),
            {"message_id": message_id},
        )
        return result.scalar_one()


async def test_the_room_callback_sends_commands_to_memory_and_everything_else_to_a_mention(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    pr = await _pr_thread(sessions, engine, ["alice"])
    on_message = room_message_handler(AgentName.LOKI, sessions)
    command = _message(pr.room, "alice", "@loki remember scope: docs/ is out", pr.thread_id)
    question = _message(pr.room, "alice", "@loki what did you note?", pr.thread_id)

    await on_message(command)
    await on_message(question)

    assert await _notes(sessions, pr) == ["scope=docs/ is out"]
    assert await _mention_jobs(engine, command.message_id) == 0
    assert await _mention_jobs(engine, question.message_id) == 1
