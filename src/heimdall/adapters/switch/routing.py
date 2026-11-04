"""Deliver outbox ``switch`` rows as the agent that wrote them, or as the bot if none did."""

from collections.abc import Mapping
from typing import Protocol

from heimdall.adapters.db.outbox.models import OutboxMessage
from heimdall.adapters.db.outbox.sender import ChannelSender
from heimdall.shared.agents import AgentName
from heimdall.shared.errors import SwitchRequestFailed

AGENT_FIELD = "agent"


class AgentPostPort(Protocol):
    """Posts room text as one agent; ``AgentPoster`` is the production implementation."""

    async def post(self, room_id: str, content: str) -> None:
        """Post ``content`` in ``room_id`` under this agent's identity."""
        ...


class SwitchRoutingSender:
    """Send a row through its agent's Switch key, or through the Mattermost bot.

    A row with no ``agent`` key keeps the old path, so existing writers are unaffected. A row
    that names an agent never falls back to the bot: a wrong or missing identity fails the row
    (it goes dead and the room is told) instead of posting under the wrong name. The bot is
    optional so agent posting works on a deployment that has agent keys but no Mattermost bot;
    a row that needs the missing bot fails the same way instead of waiting forever.
    """

    def __init__(
        self, bot: ChannelSender | None, posters: Mapping[AgentName, AgentPostPort]
    ) -> None:
        """Wire the fallback bot and one poster per configured agent.

        Args:
            bot: Sender for rows that name no agent, or ``None`` when no bot is configured.
            posters: Poster for each agent whose Switch key is configured.
        """
        self._bot = bot
        self._posters = posters

    async def send(self, message: OutboxMessage) -> None:
        """Deliver ``message`` under the identity its payload names.

        Args:
            message: A ``switch`` outbox row.

        Raises:
            SwitchRequestFailed: the agent name is invalid or has no key, the row needs the bot
                and none is configured, or the payload has no room or text. Not retryable, and
                the text never repeats payload values.
            SwitchUnavailable: Switch could not be reached; retryable.
        """
        if AGENT_FIELD not in message.payload:
            await self._send_as_bot(message)
            return
        poster = self._poster_for(message.payload[AGENT_FIELD])
        room_id, content = _room_and_text(message)
        await poster.post(room_id, content)

    async def _send_as_bot(self, message: OutboxMessage) -> None:
        """Send through the Mattermost bot, failing the row if there is none."""
        if self._bot is None:
            raise SwitchRequestFailed("no Mattermost bot is configured for this post")
        await self._bot.send(message)

    def _poster_for(self, raw_name: object) -> AgentPostPort:
        """Find the poster for the agent a payload names, or fail without echoing the value."""
        try:
            name = AgentName(raw_name)
        except ValueError:
            raise SwitchRequestFailed("Switch post names an unknown agent") from None
        poster = self._posters.get(name)
        if poster is None:
            raise SwitchRequestFailed(f"no Switch key is configured for agent {name.value}")
        return poster


def _room_and_text(message: OutboxMessage) -> tuple[str, str]:
    """Return the room id and text of an agent post, failing if either is missing."""
    room_id = message.payload.get("channel_id")
    content = message.payload.get("message")
    if not isinstance(room_id, str) or not room_id:
        raise SwitchRequestFailed("Switch post payload has no channel_id")
    if not isinstance(content, str) or not content:
        raise SwitchRequestFailed("Switch post payload has no message")
    return room_id, content
