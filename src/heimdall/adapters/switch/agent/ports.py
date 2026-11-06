"""Port for acting as one Switch agent: receive addressed messages, post as that agent."""

import asyncio
from typing import Protocol


class AgentRoomPort(Protocol):
    """What the pipeline needs from a Switch agent connection.

    Unlike ``RoomPort`` (Mattermost posts and reactions), a Switch agent only gets messages
    addressed to it and posts with a room id and text; there are no reactions or threads.
    """

    async def run(self, stop: asyncio.Event) -> None:
        """Hold the connection and deliver events until ``stop`` is set or it must end."""
        ...

    async def post_message(self, room_id: str, content: str) -> None:
        """Post ``content`` in ``room_id`` as this agent."""
        ...

    async def send_typing(self, channel_id: str) -> None:
        """Send a typing indicator in ``channel_id`` as this agent. No-op if not configured."""
        ...

    async def add_reaction(self, post_id: str, emoji_name: str) -> None:
        """Add a reaction to ``post_id`` as this agent. No-op if not configured."""
        ...

    async def remove_reaction(self, post_id: str, emoji_name: str) -> None:
        """Remove a reaction from ``post_id`` as this agent. No-op if not configured."""
        ...

    async def post_progress(
        self,
        channel_id: str,
        thread_id: str | None,
        text: str,
        *,
        update_post_id: str | None = None,
    ) -> str | None:
        """Post or update one progress message in ``channel_id`` as this agent."""
        ...

    async def post_rich(
        self,
        room_id: str,
        content: str,
        thread_id: str | None,
        *,
        attachments: list[dict[str, object]],
    ) -> str | None:
        """Post one rich message with Mattermost attachments in ``room_id`` as this agent.

        No-op (returns None) when not configured; failures are raised for the caller to retry.
        """
        ...

    async def aclose(self) -> None:
        """Release HTTP resources."""
        ...
