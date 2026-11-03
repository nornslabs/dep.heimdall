"""Read tenant repository state inside a caller's transaction."""

from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.modules.tenancy.models import Repo
from heimdall.shared.context import TenantCtx
from heimdall.shared.ids import RepoId


async def repo_is_enabled(
    session: AsyncSession,
    ctx: TenantCtx,
    repo_id: RepoId,
) -> bool:
    """Check whether a repository is enabled without leaving the current transaction.

    Args:
        session: Session inside the service's transaction.
        ctx: Tenant context that scopes the repository lookup.
        repo_id: GitHub repository identifier.

    Returns:
        Whether Heimdall currently handles the repository.
    """
    statement = select_for_tenant(Repo, ctx.installation_id).where(Repo.repo_id == repo_id)
    result = await session.exec(statement)
    repo = result.first()
    return repo is not None and repo.enabled


async def repo_switch_room_id(
    session: AsyncSession,
    ctx: TenantCtx,
    repo_id: RepoId,
) -> str | None:
    """Read the configured Switch room for one tenant repository.

    Args:
        session: Session inside the caller's transaction.
        ctx: Tenant context that scopes the repository lookup.
        repo_id: GitHub repository identifier.

    Returns:
        Switch channel id, or None when no room is configured.
    """
    statement = select_for_tenant(Repo, ctx.installation_id).where(Repo.repo_id == repo_id)
    result = await session.exec(statement)
    repo = result.first()
    return repo.switch_room_id if repo is not None else None
