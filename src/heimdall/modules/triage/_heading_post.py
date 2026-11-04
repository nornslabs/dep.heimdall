"""The outbox row that opens a PR's Switch thread with its heading."""

from uuid import UUID

from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.outbox.writer import write_outbox
from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.modules.triage.domain import PrEvent
from heimdall.modules.triage.models import Pr
from heimdall.shared.agents import AgentName
from heimdall.shared.context import TenantCtx

THREAD_HEADING_EFFECT = "thread_heading"


async def write_thread_heading(
    session: AsyncSession,
    ctx: TenantCtx,
    event: PrEvent,
    pr_id: UUID,
) -> None:
    """Queue Heimdall's heading post as the thread root, unless the PR already has a thread.

    A repo with no agent room, or an event with no heading, writes nothing. A later push that
    re-runs intake finds the saved thread and adds no second heading. Two pushes queued before
    the first heading is delivered can still each write one; that is cosmetic and accepted.

    Args:
        session: Session inside the transaction that records the state change.
        ctx: Tenant and request context.
        event: The step event; supplies the agent room and the rendered heading.
        pr_id: Triage PR row id.
    """
    if not event.agent_room_id or not event.thread_heading:
        return
    pr = (
        await session.exec(select_for_tenant(Pr, ctx.installation_id).where(Pr.id == pr_id))
    ).first()
    if pr is None or pr.switch_thread_id is not None:
        return
    await write_outbox(
        session,
        installation_id=ctx.installation_id,
        channel="switch",
        kind="room.message",
        payload={
            "agent": AgentName.HEIMDALL.value,
            "channel_id": event.agent_room_id,
            "message": event.thread_heading,
            "pr_id": str(pr_id),
            "thread_role": "root",
        },
        idempotency_key=f"triage:{event.event_id}:{THREAD_HEADING_EFFECT}",
    )
