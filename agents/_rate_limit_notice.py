"""Queue the one notice a sender gets for asking an agent too many questions, and audit it."""

from sqlmodel.ext.asyncio.session import AsyncSession

from agents._presence import format_rate_limited_reply
from heimdall.adapters.db.outbox.writer import write_outbox
from heimdall.adapters.switch.agent.protocol import AgentMessage
from heimdall.modules.audit.api import AuditEntry, append_in_transaction, pr_subject
from heimdall.modules.conversation.api import Limited
from heimdall.modules.triage.api import PrSnapshot
from heimdall.shared.agents import AgentName
from heimdall.shared.context import PrRef, TenantCtx

RATE_LIMITED_KEY_PREFIX = "rate-limited"
RATE_LIMITED_ACTION = "conversation.rate_limited"


async def queue_rate_limit_notice(
    session: AsyncSession,
    ctx: TenantCtx,
    agent: AgentName,
    message: AgentMessage,
    pr: PrSnapshot | None,
    decision: Limited,
) -> None:
    """Queue the rate-limit notice into the thread and add its audit entry.

    Both are written in the caller's transaction, so a notice never goes out unaudited. The
    audit entry names the sender, the place and the message, never what was asked.

    Args:
        session: Session inside the caller's transaction.
        ctx: Tenant and request context; supplies installation_id.
        agent: The agent that is not answering.
        message: The refused question; only its ids and sender name are used.
        pr: The PR whose thread the question was in, or None outside a PR thread.
        decision: The refusal, which says when the sender may ask again.
    """
    payload: dict[str, object] = {
        "agent": agent.value,
        "channel_id": message.room_id,
        "message": format_rate_limited_reply(agent, decision.retry_after),
    }
    if pr is not None:
        payload["pr_id"] = str(pr.id)
    await write_outbox(
        session,
        installation_id=ctx.installation_id,
        channel="switch",
        kind="room.message",
        payload=payload,
        idempotency_key=f"{RATE_LIMITED_KEY_PREFIX}:{agent.value}:{message.message_id}",
    )
    await append_in_transaction(session, ctx, _audit_entry(agent, message, pr))


def _audit_entry(agent: AgentName, message: AgentMessage, pr: PrSnapshot | None) -> AuditEntry:
    subject = (
        f"room:{message.room_id}"
        if pr is None
        else pr_subject(PrRef(repo_id=pr.repo_id, number=pr.number))
    )
    return AuditEntry(
        actor=message.sender_name,
        action=RATE_LIMITED_ACTION,
        subject=subject,
        payload={"agent": agent.value, "message_id": message.message_id},
    )
