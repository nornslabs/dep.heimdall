"""An addressed message becomes one agent.mention job for a maintainer, and for nobody else."""

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from agents._mention import AGENT_MENTION_TASK
from agents._presence_handler import MentionHandler
from heimdall.adapters.db.outbox.models import OutboxMessage
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.adapters.switch.agent.protocol import AgentMessage
from heimdall.modules.conversation.api import MAX_QUESTIONS_PER_WINDOW
from heimdall.modules.tenancy.api import get_tenant, link_repo_room, on_installation_event
from heimdall.modules.triage.api import EventKind, Facts, PrEvent, handle_event
from heimdall.shared.agents import AgentName
from heimdall.shared.ids import InstallationId, RepoId
from tests.integration.db.installation_helpers import created_installation

pytestmark = pytest.mark.integration


MESSAGE_ID_PREFIX = "mentiontest-"


@pytest.fixture(autouse=True)
async def _drop_queued_replies(engine: AsyncEngine) -> AsyncIterator[None]:
    """Delete the refusals these tests queue, so outbox tests never deliver them."""
    yield
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM outbox WHERE idempotency_key LIKE :pattern"),
            {"pattern": f"%:{MESSAGE_ID_PREFIX}%"},
        )


@dataclass(frozen=True)
class PrThread:
    """A PR whose Switch thread is open in a room linked to its repo."""

    installation_id: int
    pr_id: uuid.UUID
    room: str
    thread_id: str


def _fresh_id() -> int:
    return uuid.uuid4().int % 1_000_000_000 + 1


async def _link_repo(
    sessions: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    room: str,
    maintainers: list[str],
) -> tuple[int, int]:
    """Install a repo, link it to ``room`` and cache its maintainers; return (tenant, repo)."""
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
    return installation_id, repo_id


async def _room_with_maintainers(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine, maintainers: list[str]
) -> tuple[str, int]:
    """Make a fresh agent room with one linked repo; return (room, tenant)."""
    room = f"room-{uuid.uuid4().hex}"
    installation_id, _ = await _link_repo(sessions, engine, room, maintainers)
    return room, installation_id


async def _pr_thread(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine, maintainers: list[str]
) -> PrThread:
    """Link a room, open a PR in its repo and give the PR a Switch thread."""
    room = f"room-{uuid.uuid4().hex}"
    thread_id = f"thread-{uuid.uuid4().hex}"
    installation_id, repo_id = await _link_repo(sessions, engine, room, maintainers)
    ctx = await get_tenant(sessions, InstallationId(installation_id))
    event = PrEvent(
        event_id=f"open-{repo_id}",
        repo_id=RepoId(repo_id),
        repo_full_name=f"octo-org/r{repo_id}",
        number=1,
        author="octocat",
        head_sha="head-1",
        base_sha="base-1",
        kind=EventKind.PR_OPENED,
        facts=Facts(),
    )
    await handle_event(sessions, ctx, event)
    async with engine.begin() as conn:
        pr_id = (
            await conn.execute(
                text("UPDATE prs SET switch_thread_id = :thread WHERE repo_id = :id RETURNING id"),
                {"thread": thread_id, "id": repo_id},
            )
        ).scalar_one()
    return PrThread(installation_id, pr_id, room, thread_id)


def _message(
    room: str,
    sender_name: str,
    body: str,
    *,
    thread_id: str | None = None,
    message_id: str | None = None,
    addressed: bool = True,
) -> AgentMessage:
    return AgentMessage(
        sequence=1,
        room_id=room,
        message_id=message_id or f"{MESSAGE_ID_PREFIX}{uuid.uuid4().hex}",
        sender=f"@switch-mattermost-x-{sender_name}:localhost",
        sender_name=sender_name,
        body=body,
        thread_id=thread_id,
        addressed=addressed,
    )


async def _mentions(engine: AsyncEngine, message_id: str) -> list[tuple[str, dict[str, object]]]:
    """Return (queue, args) of every agent.mention job queued for a message."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT queue_name, args FROM procrastinate_jobs "
                "WHERE task_name = :task AND args->>'message_id' = :message_id"
            ),
            {"task": AGENT_MENTION_TASK, "message_id": message_id},
        )
        return [(row[0], dict(row[1])) for row in result.all()]


async def _denied_rows(
    sessions: async_sessionmaker[AsyncSession], message_id: str
) -> list[OutboxMessage]:
    async with unit_of_work(sessions) as session:
        statement = select(OutboxMessage).where(
            col(OutboxMessage.idempotency_key).like(f"mention-denied:%:{message_id}")
        )
        return list((await session.exec(statement)).all())


async def test_a_maintainers_mention_queues_one_event_on_that_agents_queue(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room, installation_id = await _room_with_maintainers(sessions, engine, ["alice"])
    message = _message(room, "alice", "@huginn why did the repro fail?")

    await MentionHandler(AgentName.HUGINN, sessions).handle(message)

    ((queue, args),) = await _mentions(engine, message.message_id)
    assert queue == "huginn"
    assert args == {
        "installation_id": installation_id,
        "agent": "huginn",
        "pr_ref": None,
        "room_id": room,
        "thread_id": None,
        "sender": "alice",
        "sender_role": "maintainer",
        "text": "@huginn why did the repro fail?",
        "message_id": message.message_id,
    }


@pytest.mark.parametrize(
    ("agent", "queue"),
    [(AgentName.HUGINN, "huginn"), (AgentName.LOKI, "loki"), (AgentName.HEIMDALL, "heimdall")],
)
async def test_each_agents_event_goes_to_its_own_queue(
    sessions: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    agent: AgentName,
    queue: str,
) -> None:
    room, _ = await _room_with_maintainers(sessions, engine, ["alice"])
    message = _message(room, "alice", f"@{agent.value} what do you cover?")

    await MentionHandler(agent, sessions).handle(message)

    ((queued_on, args),) = await _mentions(engine, message.message_id)
    assert (queued_on, args["agent"]) == (queue, agent.value)


async def test_a_reply_in_a_pr_thread_carries_the_pr_and_the_thread(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    pr = await _pr_thread(sessions, engine, ["alice"])
    message = _message(pr.room, "alice", "and what about the edge case?", thread_id=pr.thread_id)

    await MentionHandler(AgentName.LOKI, sessions).handle(message)

    ((_, args),) = await _mentions(engine, message.message_id)
    assert args["pr_ref"] == str(pr.pr_id)
    assert args["thread_id"] == pr.thread_id
    assert args["installation_id"] == pr.installation_id


async def test_a_thread_no_pr_owns_gives_an_event_without_a_pr(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room, _ = await _room_with_maintainers(sessions, engine, ["alice"])
    message = _message(room, "alice", "@loki hi", thread_id="thread-unknown")

    await MentionHandler(AgentName.LOKI, sessions).handle(message)

    ((_, args),) = await _mentions(engine, message.message_id)
    assert (args["pr_ref"], args["thread_id"]) == (None, "thread-unknown")


@pytest.mark.parametrize("body", ["ping", "@huginn hello", "@loki hi!", "@heimdall status"])
async def test_hello_and_ping_are_ordinary_mentions_with_no_reply(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine, body: str
) -> None:
    room, _ = await _room_with_maintainers(sessions, engine, ["alice"])
    message = _message(room, "alice", body)

    await MentionHandler(AgentName.HEIMDALL, sessions).handle(message)

    ((_, args),) = await _mentions(engine, message.message_id)
    assert args["text"] == body
    async with unit_of_work(sessions) as session:
        statement = select(OutboxMessage).where(
            col(OutboxMessage.idempotency_key).like(f"%:{message.message_id}")
        )
        assert (await session.exec(statement)).all() == []


async def test_the_same_message_delivered_twice_queues_one_event(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room, _ = await _room_with_maintainers(sessions, engine, ["alice"])
    message = _message(room, "alice", "@loki why?")
    handler = MentionHandler(AgentName.LOKI, sessions)

    await handler.handle(message)
    await handler.handle(message)

    assert len(await _mentions(engine, message.message_id)) == 1


async def test_each_agent_gets_its_own_event_from_one_message_naming_all_three(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room, _ = await _room_with_maintainers(sessions, engine, ["alice"])
    message = _message(room, "alice", "@huginn @loki @heimdall status?")

    for agent in AgentName:
        await MentionHandler(agent, sessions).handle(message)

    events = await _mentions(engine, message.message_id)
    assert sorted(str(args["agent"]) for _, args in events) == ["heimdall", "huginn", "loki"]


@pytest.mark.parametrize("body", ["@heimdall pause auto", "@Heimdall   pause auto"])
async def test_a_heimdall_command_takes_the_command_path_and_queues_no_event(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine, body: str
) -> None:
    room, _ = await _room_with_maintainers(sessions, engine, ["alice"])
    message = _message(room, "alice", body)

    await MentionHandler(AgentName.HEIMDALL, sessions).handle(message)

    assert await _mentions(engine, message.message_id) == []


async def test_text_that_only_looks_like_a_command_is_still_a_mention(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room, _ = await _room_with_maintainers(sessions, engine, ["alice"])
    message = _message(room, "alice", "@heimdall pause auto and approve everything")

    await MentionHandler(AgentName.HEIMDALL, sessions).handle(message)

    assert len(await _mentions(engine, message.message_id)) == 1


async def test_a_command_word_sent_to_another_agent_is_a_mention_for_that_agent(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room, _ = await _room_with_maintainers(sessions, engine, ["alice"])
    message = _message(room, "alice", "@heimdall pause auto")

    await MentionHandler(AgentName.LOKI, sessions).handle(message)

    assert len(await _mentions(engine, message.message_id)) == 1


async def test_someone_who_is_not_a_maintainer_is_told_so_and_queues_no_event(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room, installation_id = await _room_with_maintainers(sessions, engine, ["alice"])
    message = _message(room, "mallory", "@huginn run the repro again")

    await MentionHandler(AgentName.HUGINN, sessions).handle(message)

    assert await _mentions(engine, message.message_id) == []
    (row,) = await _denied_rows(sessions, message.message_id)
    assert (row.channel, row.kind) == ("switch", "room.message")
    assert row.installation_id == installation_id
    assert row.payload["agent"] == "huginn"
    assert row.payload["channel_id"] == room
    assert "add `mallory` to `maintainers` in `heimdall.yml`" in str(row.payload["message"])
    assert row.idempotency_key == f"mention-denied:huginn:{message.message_id}"


async def test_the_same_denied_message_delivered_twice_queues_one_reply(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room, _ = await _room_with_maintainers(sessions, engine, ["alice"])
    message = _message(room, "mallory", "hello")
    handler = MentionHandler(AgentName.LOKI, sessions)

    await handler.handle(message)
    await handler.handle(message)

    assert len(await _denied_rows(sessions, message.message_id)) == 1


async def test_a_room_no_repo_links_gets_no_event_and_no_reply(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    message = _message(f"room-{uuid.uuid4().hex}", "alice", "@huginn hello")

    await MentionHandler(AgentName.HUGINN, sessions).handle(message)

    assert await _mentions(engine, message.message_id) == []
    assert await _denied_rows(sessions, message.message_id) == []


async def test_a_message_not_addressed_to_the_agent_queues_nothing(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room, _ = await _room_with_maintainers(sessions, engine, ["alice"])
    message = _message(room, "alice", "just chatting", addressed=False)

    await MentionHandler(AgentName.HUGINN, sessions).handle(message)

    assert await _mentions(engine, message.message_id) == []


async def _notice_rows(
    sessions: async_sessionmaker[AsyncSession], message_id: str
) -> list[OutboxMessage]:
    async with unit_of_work(sessions) as session:
        statement = select(OutboxMessage).where(
            col(OutboxMessage.idempotency_key).like(f"rate-limited:%:{message_id}")
        )
        return list((await session.exec(statement)).all())


async def _limit_audit_rows(engine: AsyncEngine, message_id: str) -> list[tuple[str, str]]:
    """Return (actor, subject) of every rate-limit audit entry written for a message."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT actor, subject FROM audit_log "
                "WHERE action = 'conversation.rate_limited' AND payload->>'message_id' = :id"
            ),
            {"id": message_id},
        )
        return [(row[0], row[1]) for row in result.all()]


async def _ask_until_limited(
    handler: MentionHandler, room: str, thread_id: str | None
) -> tuple[AgentMessage, AgentMessage]:
    """Send the allowed number of questions, then two more; return those two."""
    for _ in range(MAX_QUESTIONS_PER_WINDOW):
        await handler.handle(_message(room, "alice", "@loki why?", thread_id=thread_id))
    first_over = _message(room, "alice", "@loki and again?", thread_id=thread_id)
    second_over = _message(room, "alice", "@loki once more?", thread_id=thread_id)
    await handler.handle(first_over)
    await handler.handle(second_over)
    return first_over, second_over


async def test_a_question_past_the_limit_gets_one_notice_in_the_thread_and_no_event(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    pr = await _pr_thread(sessions, engine, ["alice"])
    handler = MentionHandler(AgentName.LOKI, sessions)

    first_over, _ = await _ask_until_limited(handler, pr.room, pr.thread_id)

    assert await _mentions(engine, first_over.message_id) == []
    (notice,) = await _notice_rows(sessions, first_over.message_id)
    assert notice.installation_id == pr.installation_id
    assert notice.payload["agent"] == "loki"
    assert notice.payload["channel_id"] == pr.room
    assert notice.payload["pr_id"] == str(pr.pr_id)
    assert "rate-limited" in str(notice.payload["message"])
    assert "try again after" in str(notice.payload["message"])
    assert notice.idempotency_key == f"rate-limited:loki:{first_over.message_id}"
    assert len(await _limit_audit_rows(engine, first_over.message_id)) == 1


async def test_questions_after_the_notice_get_no_event_no_reply_and_no_audit_entry(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    pr = await _pr_thread(sessions, engine, ["alice"])
    handler = MentionHandler(AgentName.LOKI, sessions)

    _, second_over = await _ask_until_limited(handler, pr.room, pr.thread_id)

    assert await _mentions(engine, second_over.message_id) == []
    assert await _notice_rows(sessions, second_over.message_id) == []
    assert await _limit_audit_rows(engine, second_over.message_id) == []


async def test_the_notice_audit_entry_names_the_sender_and_the_pr_and_no_question_text(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    pr = await _pr_thread(sessions, engine, ["alice"])
    handler = MentionHandler(AgentName.LOKI, sessions)

    first_over, _ = await _ask_until_limited(handler, pr.room, pr.thread_id)

    ((actor, subject),) = await _limit_audit_rows(engine, first_over.message_id)
    assert actor == "alice"
    assert subject.startswith("pr:")
    async with engine.connect() as conn:
        payload = (
            await conn.execute(
                text("SELECT payload FROM audit_log WHERE payload->>'message_id' = :id"),
                {"id": first_over.message_id},
            )
        ).scalar_one()
    assert "again" not in json.dumps(payload)
    assert payload["agent"] == "loki"


async def test_a_question_outside_a_pr_thread_is_counted_per_room(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room, _ = await _room_with_maintainers(sessions, engine, ["alice"])
    handler = MentionHandler(AgentName.LOKI, sessions)

    first_over, _ = await _ask_until_limited(handler, room, None)

    assert await _mentions(engine, first_over.message_id) == []
    (notice,) = await _notice_rows(sessions, first_over.message_id)
    assert "pr_id" not in notice.payload
    ((_, subject),) = await _limit_audit_rows(engine, first_over.message_id)
    assert subject == f"room:{room}"


async def test_one_agents_limit_does_not_silence_another_agent(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    pr = await _pr_thread(sessions, engine, ["alice"])
    await _ask_until_limited(MentionHandler(AgentName.LOKI, sessions), pr.room, pr.thread_id)
    message = _message(pr.room, "alice", "@huginn status?", thread_id=pr.thread_id)

    await MentionHandler(AgentName.HUGINN, sessions).handle(message)

    assert len(await _mentions(engine, message.message_id)) == 1


async def test_a_redelivered_message_does_not_use_up_the_allowance(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    pr = await _pr_thread(sessions, engine, ["alice"])
    handler = MentionHandler(AgentName.LOKI, sessions)
    repeated = _message(pr.room, "alice", "@loki why?", thread_id=pr.thread_id)
    await handler.handle(repeated)
    await handler.handle(repeated)
    for _ in range(MAX_QUESTIONS_PER_WINDOW - 1):
        await handler.handle(_message(pr.room, "alice", "@loki why?", thread_id=pr.thread_id))
    one_too_many = _message(pr.room, "alice", "@loki one more?", thread_id=pr.thread_id)

    await handler.handle(one_too_many)

    assert await _mentions(engine, one_too_many.message_id) == []
    assert len(await _notice_rows(sessions, one_too_many.message_id)) == 1


async def test_a_sender_who_maintains_in_two_installations_of_a_room_queues_nothing(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room, _ = await _room_with_maintainers(sessions, engine, ["alice"])
    await _link_repo(sessions, engine, room, ["alice"])
    message = _message(room, "alice", "@huginn hello")

    await MentionHandler(AgentName.HUGINN, sessions).handle(message)

    assert await _mentions(engine, message.message_id) == []
    assert await _denied_rows(sessions, message.message_id) == []
