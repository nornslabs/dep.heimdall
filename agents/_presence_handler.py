"""Answer a maintainer's ping or greeting to an agent, through the outbox."""

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from agents._presence import format_presence_reply, parse_presence_prompt
from heimdall.adapters.db.outbox.writer import write_outbox
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.adapters.switch.agent.protocol import AgentMessage
from heimdall.modules.tenancy.api import find_room_maintainer
from heimdall.shared.agents import AgentName
from heimdall.shared.errors import RoomTenantAmbiguous
from heimdall.shared.logging import get_logger

log = get_logger(__name__)


class PresenceHandler:
    """Turns an addressed "ping" or "hello" from a maintainer into one outbox reply.

    The agent never posts from the event itself: the reply goes through the outbox, where the
    worker sends it under this agent's own Switch key. Everything else is ignored, including
    messages from people who are not maintainers of a repo linked to the room.
    """

    def __init__(self, agent: AgentName, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Bind the handler to one agent and the tenant database.

        Args:
            agent: The agent this handler speaks for.
            sessions: Session factory for the lookup and the outbox write.
        """
        self._agent = agent
        self._sessions = sessions

    async def handle(self, message: AgentMessage) -> None:
        """Reply to a presence prompt from a maintainer; ignore anything else.

        Args:
            message: An event from Switch. Its body is untrusted and is never logged or echoed.
        """
        if not message.addressed:
            return
        prompt = parse_presence_prompt(message.body)
        if prompt is None:
            self._ignored(message, "not_a_presence_prompt")
            return
        async with unit_of_work(self._sessions) as session:
            try:
                maintainer = await find_room_maintainer(
                    session, message.room_id, message.sender_name
                )
            except RoomTenantAmbiguous:
                self._ignored(message, "ambiguous_room")
                return
            if maintainer is None:
                self._ignored(message, "not_a_maintainer")
                return
            await write_outbox(
                session,
                installation_id=maintainer.installation_id,
                channel="switch",
                kind="room.message",
                payload={
                    "agent": self._agent.value,
                    "channel_id": message.room_id,
                    "message": format_presence_reply(self._agent, prompt),
                },
                idempotency_key=f"presence:{self._agent.value}:{message.message_id}",
            )

    def _ignored(self, message: AgentMessage, reason: str) -> None:
        """Log that a message got no reply, by ids only."""
        log.info(
            "agent_message_ignored",
            agent=self._agent.value,
            reason=reason,
            room_id=message.room_id,
            message_id=message.message_id,
        )
