"""Switch room port over Mattermost-compatible REST and WebSocket APIs."""

import asyncio
from collections.abc import AsyncIterator

import httpx

from heimdall.adapters.db.outbox.models import OutboxMessage
from heimdall.adapters.switch import _websocket
from heimdall.adapters.switch._rest import MattermostRest
from heimdall.adapters.switch.ports import RoomEvent
from heimdall.shared.errors import SwitchRequestFailed


class SwitchSender:
    """Post to Switch rooms and stream inbound posts and reactions."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Configure the server, bot token and optional isolated HTTP transport."""
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._rest = MattermostRest(base_url, token, timeout=timeout, transport=transport)

    async def open_thread(self, channel_id: str, message: str) -> str:
        """Create a root post and return its id for later replies."""
        return await self._rest.create_post(channel_id, message)

    async def post_to_thread(self, channel_id: str, root_id: str, message: str) -> str:
        """Reply beneath an existing root post and return the reply id."""
        if not root_id:
            raise SwitchRequestFailed("Switch thread reply has no root_id")
        return await self._rest.create_post(channel_id, message, root_id=root_id)

    async def post_to_role(self, channel_id: str, role: str, message: str) -> str:
        """Mention a configured room role in a channel post."""
        if not role or any(character.isspace() for character in role):
            raise SwitchRequestFailed("Switch role mention is invalid")
        return await self._rest.create_post(channel_id, f"@{role} {message}")

    async def react(self, post_id: str, emoji_name: str) -> None:
        """Add a reaction to a post as the authenticated bot."""
        if not post_id or not emoji_name:
            raise SwitchRequestFailed("Switch reaction requires post_id and emoji_name")
        await self._rest.add_reaction(post_id, emoji_name)

    async def listen(self, stop: asyncio.Event) -> AsyncIterator[RoomEvent]:
        """Yield posts and reactions until stopped, reconnecting after outages."""
        async for event in _websocket.listen(self._base_url, self._token, stop):
            yield event

    async def send(self, message: OutboxMessage) -> None:
        """Deliver an outbox post; the key is attached for delivery tracing.

        Mattermost does not enforce uniqueness for post properties, so delivery
        is at-least-once if the worker crashes after server acceptance.
        """
        channel_id, body, root_id = _post_payload(message)
        await self._rest.create_post(
            channel_id,
            body,
            root_id=root_id,
            idempotency_key=message.idempotency_key,
        )

    async def aclose(self) -> None:
        """Release pooled HTTP connections."""
        await self._rest.aclose()


def _post_payload(message: OutboxMessage) -> tuple[str, str, str]:
    """Validate the fields required to create a room post."""
    channel_id = message.payload.get("channel_id")
    body = message.payload.get("message")
    root_id = message.payload.get("root_id", "")
    if not isinstance(channel_id, str) or not channel_id:
        raise SwitchRequestFailed("Switch post payload has no channel_id")
    if not isinstance(body, str) or not body:
        raise SwitchRequestFailed("Switch post payload has no message")
    if not isinstance(root_id, str):
        raise SwitchRequestFailed("Switch post payload has invalid root_id")
    return channel_id, body, root_id
