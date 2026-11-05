"""The bridge answers ``@bifrost ping`` as a plain health check, and nothing else."""

import pytest

from apps.bridge._health import HEALTH_REPLY, HealthPingHandler, is_bifrost_ping
from heimdall.adapters.switch.ports import RoomEvent


class _FakeRoom:
    """Records the thread replies the handler asks for."""

    def __init__(self) -> None:
        self.replies: list[tuple[str, str, str]] = []

    async def post_to_thread(self, channel_id: str, root_id: str, message: str) -> str:
        self.replies.append((channel_id, root_id, message))
        return "reply-1"


def _event(message: str, *, kind: str = "posted", root_id: str = "") -> RoomEvent:
    return RoomEvent(
        kind=kind,
        channel_id="channel-1",
        post_id="post-1",
        user_id="user-1",
        root_id=root_id,
        message=message,
    )


@pytest.mark.parametrize(
    "message",
    [
        "@bifrost ping",
        "@Bifrost PING",
        "  @bifrost   ping  ",
        "@bifrost ping!",
        "@bifrost: ping?",
        "@bifrost, ping.",
    ],
)
def test_a_bare_bifrost_ping_is_recognised(message: str) -> None:
    assert is_bifrost_ping(message)


@pytest.mark.parametrize(
    "message",
    [
        "",
        "ping",
        "@bifrost",
        "@bifrost hello",
        "@bifrost ping please",
        "@bifrost ping; rm -rf /",
        "@bifrost ping $(whoami)",
        "@bifrost ping\nignore previous instructions",
        "@huginn ping",
        "@heimdall ping",
        "hey @bifrost ping",
        "@bifrost ping @bifrost ping",
        "@bifrost " + "ping" * 200,
        "@bifrost ping" + " " * 5000,
    ],
)
def test_anything_else_is_not_a_bifrost_ping(message: str) -> None:
    assert not is_bifrost_ping(message)


async def test_a_ping_is_answered_in_the_thread_it_was_sent_in() -> None:
    room = _FakeRoom()

    handled = await HealthPingHandler(room).handle(_event("@bifrost ping", root_id="root-9"))

    assert handled
    assert room.replies == [("channel-1", "root-9", HEALTH_REPLY)]


async def test_a_ping_in_the_channel_starts_a_thread_on_the_ping_itself() -> None:
    room = _FakeRoom()

    await HealthPingHandler(room).handle(_event("@bifrost ping"))

    assert room.replies == [("channel-1", "post-1", HEALTH_REPLY)]


@pytest.mark.parametrize("kind", ["reaction_added", "edited", ""])
async def test_only_a_new_post_is_answered(kind: str) -> None:
    room = _FakeRoom()

    handled = await HealthPingHandler(room).handle(_event("@bifrost ping", kind=kind))

    assert not handled
    assert room.replies == []


async def test_other_messages_are_left_for_the_next_handler() -> None:
    room = _FakeRoom()

    handled = await HealthPingHandler(room).handle(_event("@heimdall pause auto"))

    assert not handled
    assert room.replies == []


def test_the_reply_is_fixed_text_that_cannot_trigger_another_ping() -> None:
    assert not is_bifrost_ping(HEALTH_REPLY)
    assert "alive" in HEALTH_REPLY
