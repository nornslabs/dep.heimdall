"""The Switch thread each PR keeps, so every agent's view lands in one conversation."""

from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.triage.domain import PrSnapshot
from heimdall.modules.triage.models import Pr
from heimdall.modules.triage.service import get_pr_snapshot
from heimdall.shared.context import TenantCtx


async def get_switch_thread_id(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    pr_id: UUID,
) -> str | None:
    """Read the Switch thread id the PR's agents reply into.

    Args:
        sessions: Tenant database session factory.
        ctx: Tenant and request context; the lookup is scoped to its installation.
        pr_id: Triage PR row id.

    Returns:
        The thread id, or None when the PR has no thread yet or belongs to another tenant.
    """
    async with unit_of_work(sessions) as session:
        pr = (
            await session.exec(select_for_tenant(Pr, ctx.installation_id).where(Pr.id == pr_id))
        ).first()
        return pr.switch_thread_id if pr is not None else None


async def save_switch_thread_id(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    pr_id: UUID,
    thread_id: str,
) -> None:
    """Remember the PR's thread, keeping the first one if two roots race to open it.

    Args:
        sessions: Tenant database session factory.
        ctx: Tenant and request context; the row is found through its installation.
        pr_id: Triage PR row id.
        thread_id: Switch event id of the thread's root post.
    """
    async with unit_of_work(sessions) as session:
        pr = (
            await session.exec(
                select_for_tenant(Pr, ctx.installation_id).where(Pr.id == pr_id).with_for_update()
            )
        ).first()
        if pr is not None and pr.switch_thread_id is None:
            pr.switch_thread_id = thread_id


async def find_pr_by_switch_thread(
    session: AsyncSession,
    ctx: TenantCtx,
    thread_id: str,
) -> PrSnapshot | None:
    """Find the PR whose Switch thread a room message was posted in.

    A message in a PR's thread is about that PR, so this is how a chat command learns which
    repository it applies to.

    Args:
        session: Session inside the caller's transaction.
        ctx: Tenant context; another installation's PR is never returned.
        thread_id: Switch event id of the thread's root post, as a message reports it.

    Returns:
        The PR, or None when no PR of this tenant owns that thread.
    """
    statement = select_for_tenant(Pr, ctx.installation_id).where(Pr.switch_thread_id == thread_id)
    pr = (await session.exec(statement)).first()
    if pr is None:
        return None
    return await get_pr_snapshot(session, ctx, pr.id)
