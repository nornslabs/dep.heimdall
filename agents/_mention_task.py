"""The ``agent.mention`` task: answer one mention and queue the reply through the outbox.

The reply goes to the room the message came from, into the PR's thread when there is one.
The idempotency key is the message and agent, so a redelivered job never posts twice.
"""

import uuid

from procrastinate import App
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from agents._conversation import MentionAnswer, Question, answer_mention
from agents._job_context import load_agent_room_id
from agents._mention import AGENT_MENTION_TASK
from agents._mention_ports import DbMentionPorts, MentionDeps
from heimdall.adapters.db.outbox.writer import outbox_message_exists, write_outbox
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.shared.context import TenantCtx, new_request_id
from heimdall.shared.ids import InstallationId
from heimdall.shared.logging import get_logger

log = get_logger(__name__)


def register_mention_task(queue_app: App, deps: MentionDeps) -> None:
    """Register the ``agent.mention`` task that answers mentions for one agent.

    Args:
        queue_app: Procrastinate app used by the agent's worker.
        deps: The agent's scope, model, tools and database.
    """
    own_name = deps.agent.scope.agent.value

    @queue_app.task(name=AGENT_MENTION_TASK)
    async def agent_mention_task(  # pyright: ignore[reportUnusedFunction]
        installation_id: int,
        agent: str,
        pr_ref: str | None,
        thread_id: str | None,
        sender: str,
        sender_role: str,
        text: str,
        message_id: str,
        room_id: str | None = None,
    ) -> None:
        """Answer one mention; ``text`` is untrusted and never logged."""
        ids = {"installation_id": installation_id, "agent": agent, "message_id": message_id}
        if agent != own_name:
            log.warning("mention_for_another_agent", **ids)
            return
        ctx = TenantCtx(InstallationId(installation_id), new_request_id())
        key = reply_key(own_name, message_id)
        if await _already_answered(deps.sessions, ctx, key):
            # A redelivered job must not run act mode's write a second time.
            log.info("mention_already_answered", **ids)
            return
        channel = room_id or await _room_for(deps.sessions, ctx, pr_ref)
        if channel is None:
            log.info("mention_dropped_no_room", **ids)
            return
        ports = DbMentionPorts(deps, ctx, sender_role, sender)
        question = Question(pr_ref=pr_ref, sender=sender, text=text)
        ai = deps.model()
        answer = None
        if deps.approve_by_message is not None:
            answer = await deps.approve_by_message(ports, ctx, ai, question)
        if answer is None:
            answer = await answer_mention(deps.agent, ports, ai, question)
        await _queue_reply(deps.sessions, ctx, own_name, channel, key, answer)
        log.info("mention_answered", pr_ref=pr_ref, **ids)


def reply_key(agent: str, message_id: str) -> str:
    """Return the outbox idempotency key of an agent's reply to one message.

    Args:
        agent: The replying agent's name.
        message_id: Switch's id for the message.

    Returns:
        ``mention:<agent>:<message_id>``; one reply per message and agent.
    """
    return f"mention:{agent}:{message_id}"


async def _already_answered(
    sessions: async_sessionmaker[AsyncSession], ctx: TenantCtx, key: str
) -> bool:
    """Say whether this message already has a queued reply from this agent."""
    async with unit_of_work(sessions) as session:
        return await outbox_message_exists(
            session, installation_id=ctx.installation_id, idempotency_key=key
        )


async def _room_for(
    sessions: async_sessionmaker[AsyncSession], ctx: TenantCtx, pr_ref: str | None
) -> str | None:
    """Find the PR's agent room for a job queued before mentions carried their room."""
    if pr_ref is None:
        return None
    try:
        pr_id = uuid.UUID(pr_ref)
    except ValueError:
        return None
    return await load_agent_room_id(sessions, ctx, pr_id)


async def _queue_reply(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    agent: str,
    channel: str,
    key: str,
    answer: MentionAnswer,
) -> None:
    """Queue the reply for the room, into the PR's thread only when the PR is known."""
    payload: dict[str, object] = {"agent": agent, "channel_id": channel, "message": answer.text}
    if answer.pr_id is not None:
        payload["pr_id"] = answer.pr_id
    async with unit_of_work(sessions) as session:
        await write_outbox(
            session,
            installation_id=ctx.installation_id,
            channel="switch",
            kind="room.message",
            payload=payload,
            idempotency_key=key,
        )
