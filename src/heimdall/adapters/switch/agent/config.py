"""Settings for one Switch agent connection and the URLs derived from them."""

from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import uuid4

DEFAULT_QUEUE_SIZE: Final = 100
DEFAULT_DEDUPE_SIZE: Final = 1000
INITIAL_BACKOFF_SECONDS: Final = 1.0
MAX_BACKOFF_SECONDS: Final = 30.0
DRAIN_TIMEOUT_SECONDS: Final = 5.0
OPEN_TIMEOUT_SECONDS: Final = 10.0
POST_TIMEOUT_SECONDS: Final = 10.0


@dataclass(frozen=True)
class AgentConnectionConfig:
    """Everything one agent process needs to hold its Switch connection.

    Attributes:
        base_url: Switch API root, for example ``http://switch:8000``.
        agent_id: Id returned when the agent was registered.
        api_key: The agent's bearer key. Never logged; hidden from ``repr``.
        rooms: Rooms to claim when the socket opens.
        connection_id: Chosen once per process and reused on every reconnect.
        queue_size: Events buffered between the socket reader and the handler.
        dedupe_size: How many recent message ids are remembered to drop replays.
        mattermost_bot_token: Bot token for Mattermost REST API (typing, reactions, progress).
            Optional; if not set, feedback features are disabled.
        mattermost_team_id: Mattermost team ID for REST API calls. Optional.
        mattermost_base_url: Mattermost root URL (``HEIMDALL_SWITCH_URL``, for example
            ``http://mattermost:8065``). Required when bot token and team ID are set.
    """

    base_url: str
    agent_id: str
    api_key: str = field(repr=False)
    rooms: tuple[str, ...]
    connection_id: str = field(default_factory=lambda: str(uuid4()))
    queue_size: int = DEFAULT_QUEUE_SIZE
    dedupe_size: int = DEFAULT_DEDUPE_SIZE
    initial_backoff_seconds: float = INITIAL_BACKOFF_SECONDS
    max_backoff_seconds: float = MAX_BACKOFF_SECONDS
    drain_timeout_seconds: float = DRAIN_TIMEOUT_SECONDS
    mattermost_bot_token: str | None = field(default=None, repr=False)
    mattermost_team_id: str | None = field(default=None, repr=False)
    mattermost_base_url: str | None = field(default=None, repr=False)

    @property
    def message_url(self) -> str:
        """URL of ``POST /agents/{id}/message``."""
        return f"{self.base_url.rstrip('/')}/agents/{self.agent_id}/message"

    @property
    def post_message_url(self) -> str:
        """URL of ``POST /agents/{id}/ops/post_message``, the call that can post into a thread."""
        return f"{self.base_url.rstrip('/')}/agents/{self.agent_id}/ops/post_message"


def websocket_url(
    config: AgentConnectionConfig, *, start_from: int | None, expected_generation: int | None
) -> str:
    """Build the connection URL, adding resume parameters on a reconnect.

    Args:
        config: The agent's connection settings.
        start_from: Highest sequence already received, or None on the first connect.
        expected_generation: Generation from the last ``connection_state``, or None.

    Returns:
        The ``ws://`` or ``wss://`` URL to open.

    Raises:
        ValueError: ``base_url`` is not http or https.
    """
    parts = urlsplit(config.base_url.rstrip("/"))
    scheme = {"http": "ws", "https": "wss"}.get(parts.scheme)
    if scheme is None:
        raise ValueError("Switch URL must use http or https")
    query: dict[str, str] = {"connection_id": config.connection_id}
    if config.rooms:
        query["rooms"] = ",".join(config.rooms)
    if start_from is not None:
        query["start_from"] = str(start_from)
    if expected_generation is not None:
        query["expected_generation"] = str(expected_generation)
    path = f"{parts.path.rstrip('/')}/agents/{config.agent_id}/connection/ws"
    return urlunsplit((scheme, parts.netloc, path, urlencode(query, safe=","), ""))
