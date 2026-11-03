"""Resolve tenant-owned repository names to GitHub identifiers."""

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.tenancy.models import Repo
from heimdall.shared.context import TenantCtx
from heimdall.shared.ids import RepoId


async def get_repo_id(
    sessions: async_sessionmaker[AsyncSession], ctx: TenantCtx, full_name: str
) -> RepoId:
    """Resolve a repository name inside the requesting installation.

    Args:
        sessions: Tenant database session factory.
        ctx: Tenant and request context.
        full_name: Owner and repository name.

    Returns:
        GitHub repository id registered to this tenant.

    Raises:
        ValueError: The repository is not registered for this installation.
    """
    async with unit_of_work(sessions) as session:
        return await get_repo_id_in_transaction(session, ctx, full_name)


async def get_repo_id_in_transaction(
    session: AsyncSession, ctx: TenantCtx, full_name: str
) -> RepoId:
    """Resolve a repository using the caller's active transaction.

    Args:
        session: Session inside the caller's transaction.
        ctx: Tenant and request context.
        full_name: Owner and repository name.

    Returns:
        GitHub repository id registered to this tenant.

    Raises:
        ValueError: The repository is not registered for this installation.
    """
    result = await session.exec(
        select_for_tenant(Repo, ctx.installation_id).where(Repo.full_name == full_name)
    )
    repo = result.first()
    if repo is None:
        raise ValueError(f"repository {full_name} is not registered for this installation")
    return RepoId(repo.repo_id)
