"""Show that an agent is working: 👀 on a message that mentions it, ✅ once it has replied.

Switch agents cannot react, but the bridge sees every room post with its real post id. A post
that mentions an agent gets 👀 straight away. When that agent's reply arrives in the same
thread, the 👀 is swapped for ✅. Pending mentions live in memory only, so a bridge restart
can leave a 👀 behind; that is cosmetic and never blocks an answer.
"""

import re
import time
from dataclasses import dataclass
from typing import Final, Protocol

from heimdall.adapters.switch.ports import RoomEvent
from heimdall.shared.agents import AgentName
from heimdall.shared.errors import SwitchRequestFailed
from heimdall.shared.logging import get_logger

WORKING: Final = "eyes"
DONE: Final = "white_check_mark"
PENDING_TTL_SECONDS: Final = 15 * 60

_AGENT_NAMES: Final = "|".join(agent.value for agent in AgentName)
_MENTION: Final = re.compile(rf"(?<![\w.@])@({_AGENT_NAMES})\b", re.IGNORECASE)
_REPLY_PREFIX: Final = re.compile(rf"^\s*({_AGENT_NAMES}):", re.IGNORECASE)

log = get_logger(__name__)


class ReactionRoom(Protocol):
    """The two reaction calls the acknowledgement needs."""

    async def react(self, post_id: str, emoji_name: str) -> None:
        """Add the bot's reaction to a post."""
        ...

    async def unreact(self, post_id: str, emoji_name: str) -> None:
        """Remove the bot's reaction from a post."""
        ...


@dataclass(frozen=True)
class _Pending:
    """A mention that has its 👀 and is waiting for its agent's reply."""

    post_id: str
    agent: str
    created_at: float


class MentionAckHandler:
    """React to mentions of an agent, and mark them done when the agent replies."""

    def __init__(self, room: ReactionRoom) -> None:
        """Bind the room used to add and remove reactions.

        Args:
            room: Reaction operations on the shared Switch bot.
        """
        self._room = room
        self._pending: dict[str, tuple[_Pending, ...]] = {}

    async def handle(self, event: RoomEvent) -> None:
        """Acknowledge a mention, or complete the mentions an agent's reply answers.

        Args:
            event: Any room event; only posts matter. The text is untrusted and only matched
                against the agent names, never logged.
        """
        if event.kind != "posted":
            return
        reply = _REPLY_PREFIX.match(event.message)
        if reply is not None:
            await self._complete(event, reply.group(1).casefold())
            return
        mention = _MENTION.search(event.message)
        if mention is not None:
            await self._acknowledge(event, mention.group(1).casefold())

    async def _acknowledge(self, event: RoomEvent, agent: str) -> None:
        """Add 👀 and remember the mention until the agent replies in its thread."""
        if not await self._safely(self._room.react(event.post_id, WORKING)):
            return
        key = _thread_key(event)
        fresh = tuple(p for p in self._pending.get(key, ()) if _alive(p))
        self._pending[key] = (*fresh, _Pending(event.post_id, agent, time.monotonic()))

    async def _complete(self, event: RoomEvent, agent: str) -> None:
        """Swap 👀 for ✅ on this thread's pending mentions of the replying agent."""
        key = _thread_key(event)
        pending = self._pending.get(key, ())
        answered = [p for p in pending if p.agent == agent and _alive(p)]
        waiting = tuple(p for p in pending if p.agent != agent and _alive(p))
        if waiting:
            self._pending[key] = waiting
        else:
            self._pending.pop(key, None)
        for mention in answered:
            await self._safely(self._room.unreact(mention.post_id, WORKING))
            await self._safely(self._room.react(mention.post_id, DONE))

    async def _safely(self, call: object) -> bool:
        """Await a reaction call; a reaction is a courtesy, so a failure is only logged."""
        try:
            await call  # type: ignore[misc]
        except SwitchRequestFailed:
            log.warning("mention_ack_failed")
            return False
        return True


def _thread_key(event: RoomEvent) -> str:
    """Name the thread a post belongs to; a room-level post is keyed by its channel."""
    return event.root_id or event.channel_id


def _alive(pending: _Pending) -> bool:
    """Say whether a pending mention is recent enough to still be waiting for a reply."""
    return time.monotonic() - pending.created_at < PENDING_TTL_SECONDS
