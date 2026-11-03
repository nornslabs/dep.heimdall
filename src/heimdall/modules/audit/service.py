"""Use cases of the audit module."""

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.audit.domain import UsageRecord
from heimdall.modules.audit.models import Usage
from heimdall.shared.context import TenantCtx


async def record_usage(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    record: UsageRecord,
) -> None:
    """Save the tokens one agent run spent, so cost can be reported per installation.

    Args:
        sessions: Tenant database session factory.
        ctx: Tenant and request context; supplies installation_id.
        record: Agent, real model string and summed token counts.
    """
    async with unit_of_work(sessions) as session:
        session.add(
            Usage(
                installation_id=ctx.installation_id,
                pr_id=record.pr_id,
                agent=record.agent,
                model=record.model,
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
            )
        )
