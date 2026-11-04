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

    async def aclose(self) -> None:
        """Release HTTP resources."""
        ...
