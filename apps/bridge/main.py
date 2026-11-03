"""Switch bridge process for inbound room messages and reactions."""

import asyncio

from apps.bridge.approval import ApprovalReactionHandler
from heimdall.adapters.db.engine import create_engine, make_session_factory
from heimdall.adapters.github.auth import GitHubAppClients, load_private_key
from heimdall.adapters.github.reader import GitHubApiReader
from heimdall.adapters.switch import RoomEvent, SwitchSender, run_inbound_events
from heimdall.shared.logging import configure_logging, get_logger
from heimdall.shared.settings import get_settings

log = get_logger(__name__)


class _BridgeHandler:
    """Log safe event metadata before routing approval reactions."""

    def __init__(self, approvals: ApprovalReactionHandler) -> None:
        """Configure the approval reaction use case."""
        self._approvals = approvals

    async def handle(self, event: RoomEvent) -> None:
        """Log safe identifiers and pass the event to approval handling."""
        log.info(
            "switch_event_received",
            event_kind=event.kind,
            channel_id=event.channel_id,
            post_id=event.post_id,
            user_id=event.user_id,
        )
        await self._approvals.handle(event)


async def run_bridge() -> None:
    """Connect the room adapter and consume inbound messages and reactions."""
    settings = get_settings()
    if not settings.switch_url or not settings.switch_token:
        raise ValueError("Switch bridge requires HEIMDALL_SWITCH_URL and HEIMDALL_SWITCH_TOKEN")
    room = SwitchSender(settings.switch_url, settings.switch_token.get_secret_value())
    database_url = settings.database_url.get_secret_value()
    engine = create_engine(database_url)
    sessions = make_session_factory(engine)
    github_clients = GitHubAppClients(
        settings.github_app_id,
        load_private_key(settings.github_private_key_path),
    )
    handler = ApprovalReactionHandler(sessions, GitHubApiReader(github_clients), room)
    try:
        await run_inbound_events(room, _BridgeHandler(handler), asyncio.Event())
    finally:
        await room.aclose()
        await engine.dispose()


def main() -> None:
    """Run the bridge process."""
    settings = get_settings()
    configure_logging(settings.log_level)
    asyncio.run(run_bridge())


if __name__ == "__main__":
    main()
