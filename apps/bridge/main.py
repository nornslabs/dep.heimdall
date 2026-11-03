"""Switch bridge process for inbound room messages and reactions."""

import asyncio

from heimdall.adapters.switch import RoomEvent, SwitchSender, run_inbound_events
from heimdall.shared.logging import configure_logging, get_logger
from heimdall.shared.settings import get_settings

log = get_logger(__name__)


class _EventLogHandler:
    """Record event metadata until feature handlers subscribe to the bridge."""

    async def handle(self, event: RoomEvent) -> None:
        """Log event identifiers without exposing untrusted message content."""
        log.info(
            "switch_event_received",
            event_kind=event.kind,
            channel_id=event.channel_id,
            post_id=event.post_id,
            user_id=event.user_id,
        )


async def run_bridge() -> None:
    """Connect the room adapter and consume inbound messages and reactions."""
    settings = get_settings()
    if not settings.switch_url or not settings.switch_token:
        raise ValueError("Switch bridge requires HEIMDALL_SWITCH_URL and HEIMDALL_SWITCH_TOKEN")
    room = SwitchSender(settings.switch_url, settings.switch_token.get_secret_value())
    try:
        await run_inbound_events(room, _EventLogHandler(), asyncio.Event())
    finally:
        await room.aclose()


def main() -> None:
    """Run the bridge process."""
    settings = get_settings()
    configure_logging(settings.log_level)
    asyncio.run(run_bridge())


if __name__ == "__main__":
    main()
