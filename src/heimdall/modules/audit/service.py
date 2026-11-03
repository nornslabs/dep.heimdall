"""Use cases of the audit module."""

import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.audit.models import Usage
from heimdall.shared.context import TenantCtx


async def record_usage(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    pr_id: uuid.UUID | None,
    agent: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Save one model usage result in the installation's tenant scope.

    Args:
        sessions: Session factory used for the write transaction.
        ctx: Tenant and request context that supplies installation_id.
        pr_id: Pull request associated with the request, if any.
        agent: Agent name responsible for the request.
        model: Provider-reported model name.
        input_tokens: Number of input tokens consumed.
        output_tokens: Number of output tokens generated.

    Raises:
        ValueError: A name is blank or a token count is negative.
    """
    if not agent.strip() or not model.strip():
        raise ValueError("agent and model are required")
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("token counts cannot be negative")
    async with unit_of_work(sessions) as session:
        session.add(
            Usage(
                installation_id=ctx.installation_id,
                pr_id=pr_id,
                agent=agent,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )
