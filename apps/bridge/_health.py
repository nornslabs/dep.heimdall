"""Answer ``@bifrost ping`` with a fixed line, so anyone can tell the bridge is running.

Bifröst is code, not an agent: it has no model and no memory, so this is a plain health check
and the one message to it that is still matched by keyword. The reply never contains the
asker's words.
"""

import re
from typing import Final, Protocol

from heimdall.adapters.switch.ports import RoomEvent

MAX_PING_LENGTH: Final = 200
HEALTH_REPLY: Final = "Bifröst: pong, I'm alive."
_PING = re.compile(r"@bifrost[:,]?\s+ping[\s!?.]*", re.IGNORECASE)
_POSTED: Final = "posted"


class ThreadPoster(Protocol):
    """The one room operation the health check needs; ``RoomPort`` provides it."""

    async def post_to_thread(self, channel_id: str, root_id: str, message: str) -> str:
        """Reply in a thread and return the reply post id."""
        ...


def is_bifrost_ping(message: str) -> bool:
    """Decide whether a post is exactly a ping to Bifröst.

    Args:
        message: Untrusted post text.

    Returns:
        True only for ``@bifrost ping`` with optional case, mention punctuation and trailing
        ``!?.``. Extra words, shell syntax, other mentions or text over ``MAX_PING_LENGTH``
        are not a ping.
    """
    if len(message) > MAX_PING_LENGTH:
        return False
    return _PING.fullmatch(message.strip()) is not None


class HealthPingHandler:
    """Replies to a ping to Bifröst in the thread it was sent in."""

    def __init__(self, room: ThreadPoster) -> None:
        """Bind the handler to the room it replies through.

        Args:
            room: Posts the reply into the ping's thread.
        """
        self._room = room

    async def handle(self, event: RoomEvent) -> bool:
        """Answer the event if it is a ping to Bifröst.

        Args:
            event: Untrusted room event; only a new post can be a ping.

        Returns:
            True when the event was a ping and has been answered, False when another handler
            should look at it.
        """
        if event.kind != _POSTED or not is_bifrost_ping(event.message):
            return False
        root_id = event.root_id or event.post_id
        await self._room.post_to_thread(event.channel_id, root_id, HEALTH_REPLY)
        return True
