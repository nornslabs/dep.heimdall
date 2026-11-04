"""Answer a maintainer's ping or greeting to an agent, through the outbox."""

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from agents._presence import (
    format_not_maintainer_reply,
    format_presence_reply,
    parse_presence_prompt,
)
from heimdall.adapters.db.outbox.writer import write_outbox
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.adapters.switch.agent.protocol import AgentMessage
from heimdall.modules.tenancy.api import find_room_maintainer, find_room_tenant
from heimdall.shared.agents import AgentName
from heimdall.shared.errors import RoomTenantAmbiguous
from heimdall.shared.logging import get_logger

log = get_logger(__name__)


class PresenceHandler:
    """Turns an addressed "ping" or "hello" from a maintainer into one outbox reply.

    The agent never posts from the event itself: the reply goes through the outbox, where the
    worker sends it under this agent's own Switch key. Someone who is not a maintainer gets a
    fixed reply telling them to ask an admin to add them to ``heimdall.yml``. Everything else is
    ignored: other requests, and rooms no single tenant owns.
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
        """Answer a presence prompt: a pong or hello for a maintainer, a refusal for anyone else.

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
                await self._refuse(session, message)
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

    async def _refuse(self, session: AsyncSession, message: AgentMessage) -> None:
        """Tell a non-maintainer to ask an admin to add them, if the room belongs to a tenant.

        A room no enabled repo links, or one shared by several installations, has no tenant to
        write the reply for, so it stays ignored.
        """
        try:
            installation_id = await find_room_tenant(session, message.room_id)
        except RoomTenantAmbiguous:
            self._ignored(message, "ambiguous_room")
            return
        if installation_id is None:
            self._ignored(message, "unlinked_room")
            return
        await write_outbox(
            session,
            installation_id=installation_id,
            channel="switch",
            kind="room.message",
            payload={
                "agent": self._agent.value,
                "channel_id": message.room_id,
                "message": format_not_maintainer_reply(self._agent, message.sender_name),
            },
            idempotency_key=f"presence-denied:{self._agent.value}:{message.message_id}",
        )
        self._ignored(message, "not_a_maintainer_replied")

    def _ignored(self, message: AgentMessage, reason: str) -> None:
        """Log that a message got no reply, by ids only."""
        log.info(
            "agent_message_ignored",
            agent=self._agent.value,
            reason=reason,
            room_id=message.room_id,
            message_id=message.message_id,
        )
