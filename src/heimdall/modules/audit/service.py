"""Use cases of the audit module."""

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.audit.domain import AuditEntry, AuditView, UsageRecord, pr_subject
from heimdall.modules.audit.models import AuditLogEntry, Usage
from heimdall.shared.context import PrRef, TenantCtx


async def append_in_transaction(session: AsyncSession, ctx: TenantCtx, entry: AuditEntry) -> None:
    """Add an audit entry to the caller's transaction, so it commits or rolls back with the change.

    Args:
        session: Session inside the caller's transaction; this function never commits.
        ctx: Tenant and request context; supplies installation_id.
        entry: Validated actor, action, subject and payload.
    """
    session.add(
        AuditLogEntry(
            installation_id=ctx.installation_id,
            actor=entry.actor,
            action=entry.action,
            subject=entry.subject,
            payload=dict(entry.payload),
        )
    )


async def list_for_pr(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    pr_ref: PrRef,
) -> list[AuditView]:
    """Read one pull request's audit trail, oldest first, for this installation only.

    Args:
        sessions: Tenant database session factory.
        ctx: Tenant and request context; supplies installation_id.
        pr_ref: Repository id and PR number whose entries to read.

    Returns:
        The PR's entries in the order they were written.
    """
    async with unit_of_work(sessions) as session:
        result = await session.exec(
            select_for_tenant(AuditLogEntry, ctx.installation_id)
            .where(AuditLogEntry.subject == pr_subject(pr_ref))
            .order_by(col(AuditLogEntry.at), col(AuditLogEntry.id))
        )
        return [_to_view(row) for row in result.all()]


def _to_view(row: AuditLogEntry) -> AuditView:
    if row.id is None:
        raise ValueError("audit log row was read without an id")
    return AuditView(row.id, row.at, row.actor, row.action, row.subject, dict(row.payload))


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
