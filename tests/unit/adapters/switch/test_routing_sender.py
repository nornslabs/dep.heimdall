"""Tests for routing outbox posts to the agent that wrote them."""

from datetime import UTC, datetime

import pytest

from heimdall.adapters.db.columns import JsonObject
from heimdall.adapters.db.outbox.models import OutboxMessage
from heimdall.adapters.switch.routing import SwitchRoutingSender
from heimdall.shared.agents import AgentName
from heimdall.shared.errors import SwitchRequestFailed, SwitchUnavailable


class RecordingPoster:
    """Stands in for one agent's poster and remembers what it was asked to post."""

    def __init__(self, error: Exception | None = None) -> None:
        self.posts: list[tuple[str, str]] = []
        self._error = error

    async def post(self, room_id: str, content: str) -> None:
        if self._error is not None:
            raise self._error
        self.posts.append((room_id, content))

    async def post_in_thread(self, content: str, thread_id: str | None) -> str:
        raise AssertionError("a row with no pr_id must not be threaded")


class RecordingBot:
    """Stands in for the Mattermost bot sender."""

    def __init__(self) -> None:
        self.sent: list[OutboxMessage] = []

    async def send(self, message: OutboxMessage) -> None:
        self.sent.append(message)


def _message(payload: JsonObject) -> OutboxMessage:
    now = datetime.now(UTC)
    return OutboxMessage(
        installation_id=123,
        channel="switch",
        kind="room.message",
        payload=payload,
        idempotency_key="outbox-key-1",
        status="pending",
        attempts=0,
        next_attempt_at=now,
        created_at=now,
    )


def _router(
    bot: RecordingBot | None,
    posters: dict[AgentName, RecordingPoster],
) -> SwitchRoutingSender:
    return SwitchRoutingSender(bot, posters)


async def test_row_naming_an_agent_is_posted_by_that_agent() -> None:
    bot = RecordingBot()
    huginn, loki = RecordingPoster(), RecordingPoster()
    router = _router(bot, {AgentName.HUGINN: huginn, AgentName.LOKI: loki})

    await router.send(_message({"agent": "huginn", "channel_id": "room-1", "message": "verdict"}))

    assert huginn.posts == [("room-1", "verdict")]
    assert loki.posts == []
    assert bot.sent == []


async def test_row_without_an_agent_goes_through_the_bot() -> None:
    bot = RecordingBot()
    huginn = RecordingPoster()
    router = _router(bot, {AgentName.HUGINN: huginn})
    message = _message({"channel_id": "room-1", "message": "checklist"})

    await router.send(message)

    assert bot.sent == [message]
    assert huginn.posts == []


async def test_unknown_agent_name_is_rejected_without_posting() -> None:
    bot = RecordingBot()
    huginn = RecordingPoster()
    router = _router(bot, {AgentName.HUGINN: huginn})

    with pytest.raises(SwitchRequestFailed) as raised:
        await router.send(_message({"agent": "odin", "channel_id": "room-1", "message": "hi"}))

    assert raised.value.retryable is False
    assert "odin" not in str(raised.value)
    assert huginn.posts == [] and bot.sent == []


async def test_agent_without_a_configured_key_is_rejected_not_sent_by_the_bot() -> None:
    bot = RecordingBot()
    router = _router(bot, {AgentName.HUGINN: RecordingPoster()})

    with pytest.raises(SwitchRequestFailed) as raised:
        await router.send(_message({"agent": "loki", "channel_id": "room-1", "message": "hi"}))

    assert raised.value.retryable is False
    assert bot.sent == []


@pytest.mark.parametrize("agent", [None, 7, ["huginn"], ""])
async def test_agent_field_that_is_not_a_name_is_rejected(agent: object) -> None:
    bot = RecordingBot()
    router = _router(bot, {AgentName.HUGINN: RecordingPoster()})

    with pytest.raises(SwitchRequestFailed):
        await router.send(_message({"agent": agent, "channel_id": "room-1", "message": "hi"}))

    assert bot.sent == []


@pytest.mark.parametrize(
    "payload",
    [
        {"agent": "huginn", "message": "hi"},
        {"agent": "huginn", "channel_id": "", "message": "hi"},
        {"agent": "huginn", "channel_id": "room-1"},
        {"agent": "huginn", "channel_id": "room-1", "message": ""},
        {"agent": "huginn", "channel_id": 5, "message": "hi"},
    ],
)
async def test_agent_post_missing_room_or_text_is_rejected(payload: JsonObject) -> None:
    huginn = RecordingPoster()
    router = _router(RecordingBot(), {AgentName.HUGINN: huginn})

    with pytest.raises(SwitchRequestFailed) as raised:
        await router.send(_message(payload))

    assert raised.value.retryable is False
    assert huginn.posts == []


async def test_poster_outage_stays_retryable() -> None:
    huginn = RecordingPoster(error=SwitchUnavailable("down"))
    router = _router(RecordingBot(), {AgentName.HUGINN: huginn})

    with pytest.raises(SwitchUnavailable) as raised:
        await router.send(_message({"agent": "huginn", "channel_id": "room-1", "message": "hi"}))

    assert raised.value.retryable is True


async def test_agent_row_is_posted_when_no_bot_is_configured() -> None:
    huginn = RecordingPoster()
    router = _router(None, {AgentName.HUGINN: huginn})

    await router.send(_message({"agent": "huginn", "channel_id": "room-1", "message": "verdict"}))

    assert huginn.posts == [("room-1", "verdict")]


async def test_row_without_an_agent_fails_when_no_bot_is_configured() -> None:
    router = _router(None, {AgentName.HUGINN: RecordingPoster()})

    with pytest.raises(SwitchRequestFailed) as raised:
        await router.send(_message({"channel_id": "room-1", "message": "checklist"}))

    assert raised.value.retryable is False


async def test_poster_refusal_text_never_carries_the_message_body() -> None:
    huginn = RecordingPoster(error=SwitchRequestFailed("Switch returned 403"))
    router = _router(RecordingBot(), {AgentName.HUGINN: huginn})

    with pytest.raises(SwitchRequestFailed) as raised:
        await router.send(
            _message({"agent": "huginn", "channel_id": "room-1", "message": "private text"})
        )

    assert "private text" not in str(raised.value)
