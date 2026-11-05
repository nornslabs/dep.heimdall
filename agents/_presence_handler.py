"""Turn a maintainer's message to an agent into one ``agent.mention`` job on the queue."""

from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from agents._mention import (
    AGENT_MENTION_TASK,
    SENDER_ROLE_MAINTAINER,
    AgentMention,
    mention_lock,
    mention_queue,
)
from agents._presence import format_not_maintainer_reply
from agents._rate_limit_notice import queue_rate_limit_notice
from heimdall.adapters.db.outbox.models import OutboxMessage
from heimdall.adapters.db.outbox.writer import write_outbox
from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.adapters.queue.enqueue import enqueue
from heimdall.adapters.switch.agent.protocol import AgentMessage
from heimdall.modules.audit.api import AuditEntry, append_in_transaction
from heimdall.modules.conversation.api import Limited, QuestionScope, admit_question_in_transaction
from heimdall.modules.desk.api import parse_command
from heimdall.modules.tenancy.api import find_room_maintainer, find_room_tenant
from heimdall.modules.triage.api import PrSnapshot, find_pr_by_switch_thread
from heimdall.shared.agents import AgentName
from heimdall.shared.context import TenantCtx, new_request_id
from heimdall.shared.errors import RoomTenantAmbiguous
from heimdall.shared.ids import InstallationId
from heimdall.shared.logging import get_logger

log = get_logger(__name__)

NOT_A_MAINTAINER_KEY_PREFIX = "mention-denied"


class MentionHandler:
    """Queues one ``agent.mention`` job for each message a maintainer addresses to this agent.

    The agent never answers from the event itself. Hello, ping and every other message are
    treated alike: the job carries the message, and whatever picks it up decides what to do.
    Heimdall's structured commands are the exception: ``parse_command`` owns them, so they
    queue nothing. Someone who is not a maintainer gets a fixed reply through the outbox
    telling them to ask an admin, and no job. Rooms no single tenant owns are ignored.
    """

    def __init__(self, agent: AgentName, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Bind the handler to one agent and the tenant database.

        Args:
            agent: The agent this handler speaks for.
            sessions: Session factory for the lookups, the job and any refusal.
        """
        self._agent = agent
        self._sessions = sessions

    async def handle(self, message: AgentMessage) -> None:
        """Queue the mention, or refuse, or ignore the message.

        Args:
            message: An event from Switch. Its body is untrusted and is never logged or echoed.
        """
        if not message.addressed:
            return
        if self._agent is AgentName.HEIMDALL and parse_command(message.body) is not None:
            log_ignored(self._agent, message, "heimdall_command")
            return
        async with unit_of_work(self._sessions) as session:
            try:
                maintainer = await find_room_maintainer(
                    session, message.room_id, message.sender_name
                )
            except RoomTenantAmbiguous:
                log_ignored(self._agent, message, "ambiguous_room")
                return
            if maintainer is None:
                await refuse_non_maintainer(
                    session, self._agent, message, NOT_A_MAINTAINER_KEY_PREFIX
                )
                return
            ctx = TenantCtx(installation_id=maintainer.installation_id, request_id=new_request_id())
            await self._queue_mention(session, ctx, message)

    async def _queue_mention(
        self, session: AsyncSession, ctx: TenantCtx, message: AgentMessage
    ) -> None:
        """Queue the event, unless the sender is over the question limit.

        A sender over the limit costs no model call: the first refused question gets one
        notice, the rest get nothing.
        """
        pr = await self._find_pr(session, ctx, message)
        scope = QuestionScope(
            agent=self._agent,
            sender=message.sender_name,
            pr_id=None if pr is None else pr.id,
            room_id=message.room_id,
        )
        decision = await admit_question_in_transaction(
            session, ctx, scope, message.message_id, datetime.now(UTC)
        )
        if isinstance(decision, Limited):
            await self._refuse_over_limit(session, ctx, message, pr, decision)
            return
        await self._enqueue_mention(session, ctx, message, pr)

    async def _refuse_over_limit(
        self,
        session: AsyncSession,
        ctx: TenantCtx,
        message: AgentMessage,
        pr: PrSnapshot | None,
        decision: Limited,
    ) -> None:
        """Send the one rate-limit notice and audit it; later refusals only get logged."""
        if not decision.send_notice:
            log_ignored(self._agent, message, "rate_limited")
            return
        await queue_rate_limit_notice(session, ctx, self._agent, message, pr, decision)
        log_ignored(self._agent, message, "rate_limited_replied")

    async def _enqueue_mention(
        self,
        session: AsyncSession,
        ctx: TenantCtx,
        message: AgentMessage,
        pr: PrSnapshot | None,
    ) -> None:
        """Enqueue the event, unless a job for this message is already waiting.

        Switch can deliver a message again after a gap. A queueing lock drops the repeat while
        the first job is still waiting; once it has run, a late repeat would queue a second
        job, so delivery is at-least-once.
        """
        mention = AgentMention(
            agent=self._agent,
            pr_ref=None if pr is None else str(pr.id),
            room_id=message.room_id,
            thread_id=message.thread_id,
            sender=message.sender_name,
            sender_role=SENDER_ROLE_MAINTAINER,
            text=message.body,
            message_id=message.message_id,
        )
        try:
            async with session.begin_nested():
                await enqueue(
                    session,
                    AGENT_MENTION_TASK,
                    mention.to_job_args(ctx.installation_id),
                    queue=mention_queue(self._agent),
                    queueing_lock=mention_lock(self._agent, message.message_id),
                )
        except IntegrityError:
            log_ignored(self._agent, message, "duplicate_mention")

    @staticmethod
    async def _find_pr(
        session: AsyncSession, ctx: TenantCtx, message: AgentMessage
    ) -> PrSnapshot | None:
        """Return the PR that owns the message's thread, if there is one."""
        if message.thread_id is None:
            return None
        return await find_pr_by_switch_thread(session, ctx, message.thread_id)


async def reply_is_queued(
    session: AsyncSession, installation_id: InstallationId, idempotency_key: str
) -> bool:
    """Tell whether a reply with this idempotency key is already in the outbox.

    A handler that writes more than the reply (an audit entry, a note) uses this to recognise a
    message Switch delivered again, so the second delivery changes nothing.

    Args:
        session: Session inside the caller's transaction.
        installation_id: The tenant the reply belongs to.
        idempotency_key: The key the reply is queued under.

    Returns:
        True when the first delivery already queued its reply.
    """
    statement = select_for_tenant(OutboxMessage, installation_id).where(
        OutboxMessage.idempotency_key == idempotency_key
    )
    return (await session.exec(statement)).first() is not None


async def refuse_non_maintainer(
    session: AsyncSession,
    agent: AgentName,
    message: AgentMessage,
    key_prefix: str,
    audit: AuditEntry | None = None,
) -> None:
    """Tell a non-maintainer to ask an admin to add them, if the room belongs to a tenant.

    A room no enabled repo links, or one shared by several installations, has no tenant to
    write the reply for, so it stays ignored.

    Args:
        session: Session inside the caller's transaction.
        agent: The agent answering.
        message: The refused message; only its ids and sender name are used.
        key_prefix: Start of the reply's idempotency key, naming the handler that refused.
        audit: An entry to record with the refusal. It is written once: a message delivered
            again, whose reply is already queued, adds nothing.
    """
    try:
        installation_id = await find_room_tenant(session, message.room_id)
    except RoomTenantAmbiguous:
        log_ignored(agent, message, "ambiguous_room")
        return
    if installation_id is None:
        log_ignored(agent, message, "unlinked_room")
        return
    idempotency_key = f"{key_prefix}:{agent.value}:{message.message_id}"
    if audit is not None:
        if await reply_is_queued(session, installation_id, idempotency_key):
            log_ignored(agent, message, "duplicate_refusal")
            return
        ctx = TenantCtx(installation_id=installation_id, request_id=new_request_id())
        await append_in_transaction(session, ctx, audit)
    await write_outbox(
        session,
        installation_id=installation_id,
        channel="switch",
        kind="room.message",
        payload={
            "agent": agent.value,
            "channel_id": message.room_id,
            "message": format_not_maintainer_reply(agent, message.sender_name),
        },
        idempotency_key=idempotency_key,
    )
    log_ignored(agent, message, "not_a_maintainer_replied")


def log_ignored(agent: AgentName, message: AgentMessage, reason: str) -> None:
    """Log that a message got no normal answer, by ids only; the body is never logged."""
    log.info(
        "agent_message_ignored",
        agent=agent.value,
        reason=reason,
        room_id=message.room_id,
        message_id=message.message_id,
    )
