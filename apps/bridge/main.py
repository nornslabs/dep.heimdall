"""Switch bridge process for inbound room messages and reactions."""

import asyncio

from apps.bridge._ack import MentionAckHandler
from apps.bridge._health import HealthPingHandler
from apps.bridge.approval import ApprovalReactionHandler
from heimdall.adapters.db.engine import create_engine, make_session_factory
from heimdall.adapters.github.auth import GitHubAppClients, load_private_key
from heimdall.adapters.github.reader import GitHubApiReader
from heimdall.adapters.switch import RoomEvent, SwitchSender, run_inbound_events
from heimdall.shared.logging import configure_logging, get_logger
from heimdall.shared.settings import get_settings

log = get_logger(__name__)


class _BridgeHandler:
    """Log safe event metadata, answer a ping to Bifröst, and route approvals otherwise."""

    def __init__(
        self,
        approvals: ApprovalReactionHandler,
        health: HealthPingHandler,
        acknowledgements: MentionAckHandler,
    ) -> None:
        """Configure the approval reaction use case, the health check and mention reactions."""
        self._approvals = approvals
        self._health = health
        self._acknowledgements = acknowledgements

    async def handle(self, event: RoomEvent) -> None:
        """Log safe identifiers, then answer a health ping or pass the event to approvals."""
        log.info(
            "switch_event_received",
            event_kind=event.kind,
            channel_id=event.channel_id,
            post_id=event.post_id,
            user_id=event.user_id,
        )
        if await self._health.handle(event):
            return
        await self._acknowledgements.handle(event)
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
        bridge = _BridgeHandler(handler, HealthPingHandler(room), MentionAckHandler(room))
        await run_inbound_events(room, bridge, asyncio.Event())
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
