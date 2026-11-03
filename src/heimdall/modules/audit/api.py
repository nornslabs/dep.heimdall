"""Public interface of the audit module.

Other modules may import only from this file.
"""

import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.modules.audit.service import record_usage as _record_usage
from heimdall.shared.context import TenantCtx

__all__ = ["record_usage"]


async def record_usage(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    pr_id: uuid.UUID | None,
    agent: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Persist model token counts for per-tenant usage reporting.

    Args:
        sessions: Session factory used for the write transaction.
        ctx: Tenant and request context that supplies installation_id.
        pr_id: Pull request associated with the request, if any.
        agent: Agent name responsible for the request.
        model: Provider-reported model name.
        input_tokens: Number of input tokens consumed.
        output_tokens: Number of output tokens generated.
    """
    await _record_usage(sessions, ctx, pr_id, agent, model, input_tokens, output_tokens)
