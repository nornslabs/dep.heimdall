"""Read tenant repository state inside a caller's transaction."""

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
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


async def repo_auto_suggest_paused(session: AsyncSession, ctx: TenantCtx, repo_id: RepoId) -> bool:
    """Read the repository's maintainer-controlled auto-suggest pause flag."""
    statement = select_for_tenant(Repo, ctx.installation_id).where(Repo.repo_id == repo_id)
    result = await session.exec(statement)
    repo = result.first()
    return repo is None or repo.auto_suggest_paused


async def set_auto_suggest_paused(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    repo_id: RepoId,
    paused: bool,
) -> None:
    """Persist whether automatic mechanical suggestions are paused for one repo."""
    async with unit_of_work(sessions) as session:
        statement = (
            select_for_tenant(Repo, ctx.installation_id)
            .where(Repo.repo_id == repo_id)
            .with_for_update()
        )
        result = await session.exec(statement)
        repo = result.first()
        if repo is None:
            raise ValueError("repository is not registered for this installation")
        repo.auto_suggest_paused = paused


async def set_auto_suggest_paused_in_transaction(
    session: AsyncSession,
    ctx: TenantCtx,
    repo_id: RepoId,
    paused: bool,
) -> None:
    """Update the policy pause inside an existing tenant transaction."""
    statement = (
        select_for_tenant(Repo, ctx.installation_id)
        .where(Repo.repo_id == repo_id)
        .with_for_update()
    )
    result = await session.exec(statement)
    repo = result.first()
    if repo is None:
        raise ValueError("repository is not registered for this installation")
    repo.auto_suggest_paused = paused


async def link_repo_room(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    repo_id: RepoId,
    room_id: str,
) -> None:
    """Point one repository at the Switch room where its triage is posted.

    Args:
        sessions: Tenant database session factory.
        ctx: Tenant context that scopes the repository lookup.
        repo_id: GitHub repository identifier.
        room_id: Switch channel id; replaces any room linked before.

    Raises:
        ValueError: the repository is not registered for this installation.
    """
    async with unit_of_work(sessions) as session:
        statement = (
            select_for_tenant(Repo, ctx.installation_id)
            .where(Repo.repo_id == repo_id)
            .with_for_update()
        )
        result = await session.exec(statement)
        repo = result.first()
        if repo is None:
            raise ValueError("repository is not registered for this installation")
        repo.switch_room_id = room_id
