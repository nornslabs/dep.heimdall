"""Public room boundary and typed events for the Switch adapter."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from heimdall.adapters.db.outbox.models import OutboxMessage


@dataclass(frozen=True)
class RoomEvent:
    """A post or reaction delivered from a Switch room."""

    kind: str
    channel_id: str
    post_id: str
    user_id: str
    root_id: str = ""
    message: str = ""
    emoji_name: str = ""


@dataclass(frozen=True)
class RoomPost:
    """Post fields needed to process a reaction without trusting event text.

    Attributes:
        id: Mattermost post identifier.
        channel_id: Room containing the post.
        root_id: Thread root identifier, or an empty string for a root post.
        message: Untrusted post text, parsed only for the strict approval marker.
    """

    id: str
    channel_id: str
    root_id: str
    message: str


class RoomPort(Protocol):
    """Operations the bridge needs from its shared room service."""

    async def open_thread(self, channel_id: str, message: str) -> str:
        """Create a root post and return its post id."""
        ...

    async def post_to_thread(self, channel_id: str, root_id: str, message: str) -> str:
        """Reply in a thread and return the reply post id."""
        ...

    async def post_to_role(self, channel_id: str, role: str, message: str) -> str:
        """Mention a room role in a channel post and return its post id."""
        ...

    async def react(self, post_id: str, emoji_name: str) -> None:
        """Add the bot's reaction to a post."""
        ...

    def listen(self, stop: asyncio.Event) -> AsyncIterator[RoomEvent]:
        """Yield room events and reconnect until ``stop`` is set."""
        ...

    async def get_post(self, post_id: str) -> RoomPost:
        """Fetch a post so a reaction can be matched to its approval marker."""
        ...

    async def get_username(self, user_id: str) -> str:
        """Resolve a Switch user id to the corresponding GitHub login."""
        ...

    async def send(self, message: OutboxMessage) -> None:
        """Deliver one outbox post."""
        ...
