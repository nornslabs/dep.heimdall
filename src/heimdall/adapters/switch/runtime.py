"""Run inbound Switch room events through the bridge's handler."""

import asyncio
from typing import Protocol

from heimdall.adapters.switch.ports import RoomEvent, RoomPort
from heimdall.shared.logging import get_logger

log = get_logger(__name__)


class RoomEventHandler(Protocol):
    """Consumes one room event without coupling the transport to business logic."""

    async def handle(self, event: RoomEvent) -> None:
        """Handle a message or reaction from a Switch room."""
        ...


async def run_inbound_events(
    room: RoomPort,
    handler: RoomEventHandler,
    stop: asyncio.Event,
) -> None:
    """Forward room events until stopped, logging only safe identifiers on failure."""
    async for event in room.listen(stop):
        try:
            await handler.handle(event)
        except Exception as error:
            log.error(
                "switch_event_handler_failed",
                event_kind=event.kind,
                channel_id=event.channel_id,
                post_id=event.post_id,
                error_type=type(error).__name__,
            )
