import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.outbox._retry import RETRY_DELAYS
from heimdall.adapters.db.outbox.models import OutboxMessage
from heimdall.adapters.db.outbox.sender import ChannelSender, run_outbox_loop, send_due_messages
from heimdall.adapters.db.outbox.writer import write_outbox
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.shared.errors import DraftChanged, GitHubUnavailable
from heimdall.shared.ids import InstallationId

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


class RecordingSender:
    """Records what it sent; for ``fail_key`` it raises the queued errors first.

    Tests share one schema, so other tests' pending messages may reach this sender too;
    only the message under test gets the errors.
    """

    def __init__(
        self,
        errors: list[Exception] | None = None,
        delay: float = 0.0,
        fail_key: str | None = None,
    ) -> None:
        self.sent: list[str] = []
        self.errors = list(errors or [])
        self.delay = delay
        self.fail_key = fail_key

    async def send(self, message: OutboxMessage) -> None:
        await asyncio.sleep(self.delay)
        if self.errors and message.idempotency_key == self.fail_key:
            raise self.errors.pop(0)
        self.sent.append(message.idempotency_key)


async def _write(
    sessions: async_sessionmaker[AsyncSession],
    channel: str = "github",
    payload: dict[str, object] | None = None,
) -> str:
    key = f"test-{uuid.uuid4().hex}"
    async with unit_of_work(sessions) as session:
        await write_outbox(
            session,
            installation_id=InstallationId(55),
            channel=channel,
            kind="pr.comment",
            payload=payload or {"body": "thanks"},
            idempotency_key=key,
        )
    return key


async def _load(sessions: async_sessionmaker[AsyncSession], key: str) -> OutboxMessage:
    async with unit_of_work(sessions) as session:
        rows = await session.exec(select(OutboxMessage).where(OutboxMessage.idempotency_key == key))
        return rows.one()


async def _send(
    sessions: async_sessionmaker[AsyncSession],
    senders: dict[str, ChannelSender],
    now: datetime = NOW,
) -> int:
    return await send_due_messages(sessions, senders, now=now)


async def test_sent_message_is_marked_sent(sessions: async_sessionmaker[AsyncSession]) -> None:
    key = await _write(sessions)
    github = RecordingSender()

    await _send(sessions, {"github": github}, now=datetime.now(UTC) + timedelta(seconds=1))

    message = await _load(sessions, key)
    assert key in github.sent
    assert message.status == "sent"
    assert message.sent_at is not None


async def test_failed_message_is_retried_after_backoff(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    key = await _write(sessions)
    now = datetime.now(UTC) + timedelta(seconds=1)

    await _send(
        sessions, {"github": RecordingSender([GitHubUnavailable("502")], fail_key=key)}, now=now
    )

    message = await _load(sessions, key)
    assert message.status == "pending"
    assert message.attempts == 1
    assert message.next_attempt_at == now + timedelta(seconds=2)
    assert message.last_error == "GitHubUnavailable"


async def _kill(sessions: async_sessionmaker[AsyncSession], channel: str, key: str) -> None:
    """Fail one message through every retry until it is dead."""
    sends = 1 + len(RETRY_DELAYS)  # the first try, then one retry per delay
    sender = RecordingSender([GitHubUnavailable("502") for _ in range(sends)], fail_key=key)
    now = datetime.now(UTC) + timedelta(seconds=1)
    for _ in range(sends):
        await _send(sessions, {channel: sender}, now=now)
        now += timedelta(minutes=11)


async def test_dead_room_message_alerts_its_room_in_words(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    key = await _write(sessions, "switch", {"channel_id": "room-9", "message": "checklist"})

    await _kill(sessions, "switch", key)

    message = await _load(sessions, key)
    alert = await _load(sessions, f"alert:{message.id}")
    assert message.status == "dead"
    assert message.attempts == 1 + len(RETRY_DELAYS)
    assert (alert.channel, alert.kind, alert.installation_id) == ("switch", "alert.outbox_dead", 55)
    assert alert.payload["channel_id"] == "room-9"
    text = alert.payload["message"]
    assert isinstance(text, str)
    assert str(message.id) in text
    assert "GitHubUnavailable" in text
    assert "checklist" not in text  # the dead message's content is never echoed back


async def test_dead_message_without_a_room_raises_no_alert(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    key = await _write(sessions)

    await _kill(sessions, "github", key)

    message = await _load(sessions, key)
    assert message.status == "dead"
    async with unit_of_work(sessions) as session:
        rows = await session.exec(
            select(OutboxMessage).where(OutboxMessage.idempotency_key == f"alert:{message.id}")
        )
        assert rows.all() == []


async def test_non_retryable_error_dies_immediately(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    key = await _write(sessions)

    await _send(
        sessions,
        {"github": RecordingSender([DraftChanged("hash")], fail_key=key)},
        now=datetime.now(UTC) + timedelta(seconds=1),
    )

    assert (await _load(sessions, key)).status == "dead"


async def test_message_without_a_sender_stays_pending(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    key = await _write(sessions, channel="switch")

    await _send(sessions, {"github": RecordingSender()}, now=datetime.now(UTC) + timedelta(1))

    message = await _load(sessions, key)
    assert message.status == "pending"
    assert message.attempts == 0


async def test_message_is_not_sent_before_its_retry_time(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    key = await _write(sessions)
    github = RecordingSender()

    await _send(sessions, {"github": github}, now=datetime.now(UTC) - timedelta(minutes=5))

    assert key not in github.sent


async def test_two_senders_never_send_the_same_message(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    key = await _write(sessions)
    first, second = RecordingSender(delay=0.2), RecordingSender(delay=0.2)
    now = datetime.now(UTC) + timedelta(seconds=1)

    await asyncio.gather(
        _send(sessions, {"github": first}, now=now),
        _send(sessions, {"github": second}, now=now),
    )

    assert (first.sent + second.sent).count(key) == 1


async def test_outbox_loop_sends_messages_and_stops_when_asked(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    key = await _write(sessions, channel="loop-test")
    sender = RecordingSender()
    stop = asyncio.Event()

    loop = asyncio.create_task(run_outbox_loop(sessions, {"loop-test": sender}, stop))
    for _ in range(50):
        if key in sender.sent:
            break
        await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(loop, timeout=3)

    assert key in sender.sent
