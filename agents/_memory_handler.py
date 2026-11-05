"""Carry out a maintainer's ``remember`` / ``forget`` command and queue the agent's reply.

The note and the reply are written in one transaction. The reply goes through the outbox into
the PR's thread under this agent's own Switch key. The note's repo is the PR that owns the
thread the command was sent in, so a room holding several repos can never get a note filed
under the wrong one.
"""

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from agents._memory_audit import (
    FORGOT_ACTION,
    REMEMBERED_ACTION,
    RefusalReason,
    denied_entry,
    record_memory_audit,
    record_refusal,
)
from agents._memory_commands import (
    Forget,
    ListNotes,
    MemoryCommand,
    Remember,
    Usage,
    format_forgot_reply,
    format_invalid_reply,
    format_no_such_note_reply,
    format_noted_reply,
    format_notes_reply,
    format_outside_thread_reply,
    format_usage_reply,
    parse_memory_command,
)
from agents._presence import format_not_maintainer_reply
from agents._presence_handler import log_ignored, refuse_non_maintainer, reply_is_queued
from heimdall.adapters.db.outbox.writer import write_outbox
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.adapters.switch.agent.protocol import AgentMessage
from heimdall.modules.memory.api import (
    NoteScope,
    forget_note_in_transaction,
    recall_notes_in_transaction,
    remember_note_in_transaction,
    validate_key,
)
from heimdall.modules.tenancy.api import find_room_maintainer
from heimdall.modules.triage.api import PrSnapshot, find_pr_by_switch_thread
from heimdall.shared.agents import AgentName
from heimdall.shared.context import TenantCtx, new_request_id
from heimdall.shared.errors import (
    InvalidNote,
    NotAMaintainer,
    NoteLimitReached,
    NoteNotFound,
    RepoNotFound,
    RoomTenantAmbiguous,
)


class MemoryCommandHandler:
    """Turns a maintainer's memory command into a note change and one outbox reply."""

    def __init__(self, agent: AgentName, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Bind the handler to one agent and the tenant database.

        Args:
            agent: The agent whose notes the commands change.
            sessions: Session factory for the lookups, the note and the reply.
        """
        self._agent = agent
        self._sessions = sessions

    async def handle(self, message: AgentMessage) -> bool:
        """Carry out a memory command addressed to this agent.

        Args:
            message: An event from Switch. Its body is untrusted and is never logged.

        Returns:
            True when the message was a memory command (answered or deliberately ignored),
            False when it is something else and another handler should look at it.
        """
        if not message.addressed:
            return False
        command = parse_memory_command(message.body)
        if command is None:
            return False
        async with unit_of_work(self._sessions) as session:
            await self._answer(session, message, command)
        return True

    async def _answer(
        self, session: AsyncSession, message: AgentMessage, command: MemoryCommand
    ) -> None:
        """Check who is asking and where, apply the command and queue the reply."""
        try:
            maintainer = await find_room_maintainer(session, message.room_id, message.sender_name)
        except RoomTenantAmbiguous:
            log_ignored(self._agent, message, "ambiguous_room")
            return
        if maintainer is None:
            await refuse_non_maintainer(
                session,
                self._agent,
                message,
                "memory-denied",
                audit=denied_entry(self._agent, message, command),
            )
            return
        ctx = TenantCtx(installation_id=maintainer.installation_id, request_id=new_request_id())
        if await reply_is_queued(session, ctx.installation_id, self._reply_key(message)):
            log_ignored(self._agent, message, "duplicate_command")
            return
        pr = None
        if message.thread_id is not None:
            pr = await find_pr_by_switch_thread(session, ctx, message.thread_id)
        reply = await self._apply(session, ctx, pr, message, command)
        await self._reply(session, ctx, pr, message, reply)

    async def _apply(
        self,
        session: AsyncSession,
        ctx: TenantCtx,
        pr: PrSnapshot | None,
        message: AgentMessage,
        command: MemoryCommand,
    ) -> str:
        """Run the command, changing a note or listing them, and return the reply text.

        Every note change and every refusal of one adds an audit entry in the same transaction.
        Listing and usage help change nothing, so they leave none.
        """
        if isinstance(command, Usage):
            return format_usage_reply(self._agent)
        if pr is None:
            if not isinstance(command, ListNotes):
                await record_refusal(
                    session, ctx, self._agent, message, pr, command, RefusalReason.OUTSIDE_THREAD
                )
            return format_outside_thread_reply(self._agent)
        try:
            return await self._run(
                session, ctx, NoteScope(pr.repo_id, self._agent), pr, message, command
            )
        except NoteLimitReached as error:
            await record_refusal(session, ctx, self._agent, message, pr, command, RefusalReason.CAP)
            return format_invalid_reply(self._agent, str(error))
        except InvalidNote as error:
            await record_refusal(
                session, ctx, self._agent, message, pr, command, RefusalReason.INVALID
            )
            return format_invalid_reply(self._agent, str(error))
        except NotAMaintainer:
            await record_refusal(
                session, ctx, self._agent, message, pr, command, RefusalReason.NOT_A_MAINTAINER
            )
            return format_not_maintainer_reply(self._agent, message.sender_name)
        except RepoNotFound:
            await record_refusal(
                session, ctx, self._agent, message, pr, command, RefusalReason.REPO_NOT_ENABLED
            )
            return format_not_maintainer_reply(self._agent, message.sender_name)

    async def _run(
        self,
        session: AsyncSession,
        ctx: TenantCtx,
        scope: NoteScope,
        pr: PrSnapshot,
        message: AgentMessage,
        command: ListNotes | Remember | Forget,
    ) -> str:
        """Carry out a command that has a repo to act on; the memory module may refuse it."""
        if isinstance(command, ListNotes):
            notes = await recall_notes_in_transaction(session, ctx, scope)
            return format_notes_reply(self._agent, notes)
        if isinstance(command, Remember):
            reply = await self._remember(session, ctx, scope, message, command)
            await record_memory_audit(
                session, ctx, self._agent, message, pr, REMEMBERED_ACTION, key=command.key
            )
            return reply
        return await self._forget(session, ctx, scope, pr, message, command)

    async def _remember(
        self,
        session: AsyncSession,
        ctx: TenantCtx,
        scope: NoteScope,
        message: AgentMessage,
        command: Remember,
    ) -> str:
        """Keep the note and return the confirmation."""
        note = await remember_note_in_transaction(
            session,
            ctx,
            scope,
            command.key,
            command.value,
            message.sender_name,
            message.message_id,
        )
        return format_noted_reply(self._agent, note.key)

    async def _forget(
        self,
        session: AsyncSession,
        ctx: TenantCtx,
        scope: NoteScope,
        pr: PrSnapshot,
        message: AgentMessage,
        command: Forget,
    ) -> str:
        """Drop the note and return the confirmation, or say there was none."""
        try:
            key = await forget_note_in_transaction(
                session, ctx, scope, command.key, message.sender_name
            )
        except NoteNotFound:
            await record_refusal(
                session, ctx, self._agent, message, pr, command, RefusalReason.NOT_FOUND
            )
            return format_no_such_note_reply(self._agent, validate_key(command.key))
        await record_memory_audit(session, ctx, self._agent, message, pr, FORGOT_ACTION, key=key)
        return format_forgot_reply(self._agent, key)

    def _reply_key(self, message: AgentMessage) -> str:
        """The idempotency key of this message's reply, which also marks it as handled."""
        return f"memory:{self._agent.value}:{message.message_id}"

    async def _reply(
        self,
        session: AsyncSession,
        ctx: TenantCtx,
        pr: PrSnapshot | None,
        message: AgentMessage,
        reply: str,
    ) -> None:
        """Queue the reply, into the PR's thread when the command came from one."""
        payload: dict[str, object] = {
            "agent": self._agent.value,
            "channel_id": message.room_id,
            "message": reply,
        }
        if pr is not None:
            payload["pr_id"] = str(pr.id)
        await write_outbox(
            session,
            installation_id=ctx.installation_id,
            channel="switch",
            kind="room.message",
            payload=payload,
            idempotency_key=self._reply_key(message),
        )
