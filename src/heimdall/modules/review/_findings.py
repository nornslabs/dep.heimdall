"""Read current-head findings for agent context."""

import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.review.domain import FindingRef
from heimdall.modules.review.models import Finding
from heimdall.modules.triage.api import get_pr_snapshot
from heimdall.shared.context import TenantCtx


async def list_findings(
    sessions: async_sessionmaker[AsyncSession], ctx: TenantCtx, pr_id: uuid.UUID
) -> tuple[FindingRef, ...]:
    """List review findings for this tenant's current PR head.

    Args:
        sessions: Tenant database session factory.
        ctx: Tenant and request context.
        pr_id: Pull request whose findings are requested.

    Returns:
        Saved findings for the PR's current head, or an empty tuple when absent.
    """
    async with unit_of_work(sessions) as session:
        snapshot = await get_pr_snapshot(session, ctx, pr_id)
        if snapshot is None:
            return ()
        result = await session.exec(
            select_for_tenant(Finding, ctx.installation_id).where(
                Finding.pr_id == pr_id,
                Finding.head_sha == snapshot.head_sha,
            )
        )
        return tuple(
            FindingRef(
                id=finding.id,
                file=finding.file,
                line=finding.line,
                text=finding.text,
                severity=finding.severity,
                mechanical_category=finding.mechanical_category,
            )
            for finding in result
        )
