"""Read the intake rules that failed for a PR head, for the reply drafted to its author."""

import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.intake.domain import RuleName, RuleResult
from heimdall.modules.intake.models import IntakeResult
from heimdall.shared.context import TenantCtx

_RULE_ORDER = {rule.value: position for position, rule in enumerate(RuleName)}


async def list_failed_rules(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    pr_id: uuid.UUID,
    head_sha: str,
) -> tuple[RuleResult, ...]:
    """Return the failed, non-stale intake rules for one PR head in checklist order.

    Args:
        sessions: Tenant database session factory.
        ctx: Tenant and request context; the query is scoped to its installation.
        pr_id: Triage PR row id.
        head_sha: Head commit whose results are wanted.

    Returns:
        Failed rules; empty when every rule passed or intake has not stored results yet.
    """
    async with unit_of_work(sessions) as session:
        rows = await session.exec(
            select_for_tenant(IntakeResult, ctx.installation_id).where(
                IntakeResult.pr_id == pr_id,
                IntakeResult.head_sha == head_sha,
                col(IntakeResult.passed).is_(False),
                col(IntakeResult.stale).is_(False),
            )
        )
        failed = sorted(rows, key=lambda row: _RULE_ORDER.get(row.rule, len(_RULE_ORDER)))
    return tuple(
        RuleResult(RuleName(row.rule), passed=False, detail=row.detail or "") for row in failed
    )
