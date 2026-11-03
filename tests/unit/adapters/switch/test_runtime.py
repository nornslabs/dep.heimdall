"""Tests for inbound Switch event delivery."""

import asyncio
from collections.abc import AsyncIterator

from heimdall.adapters.switch import RoomEvent, run_inbound_events


class FakeRoom:
    """Yield a fixed event sequence through the room port."""

    def __init__(self, events: list[RoomEvent]) -> None:
        self._events = events

    def listen(self, stop: asyncio.Event) -> AsyncIterator[RoomEvent]:
        """Yield configured events and stop after the final event."""

        async def _events() -> AsyncIterator[RoomEvent]:
            for event in self._events:
                yield event
            stop.set()

        return _events()


class RecordingHandler:
    """Capture inbound events for assertions."""

    def __init__(self) -> None:
        self.events: list[RoomEvent] = []

    async def handle(self, event: RoomEvent) -> None:
        self.events.append(event)


async def test_bridge_forwards_inbound_events_to_handler() -> None:
    """Typed posts and reactions reach the registered feature handler."""
    incoming = [
        RoomEvent(
            kind="posted",
            channel_id="room-1",
            post_id="post-1",
            user_id="user-1",
            message="@heimdall review",
        ),
        RoomEvent(
            kind="reaction_added",
            channel_id="room-1",
            post_id="post-1",
            user_id="user-1",
            emoji_name="+1",
        ),
    ]
    handler = RecordingHandler()
    stop = asyncio.Event()

    await run_inbound_events(FakeRoom(incoming), handler, stop)  # type: ignore[arg-type]

    assert handler.events == incoming
    assert stop.is_set()
