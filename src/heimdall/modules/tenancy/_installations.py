"""Installation and repository lifecycle persistence."""

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.tenancy.domain import InstallationEvent, InstallationRepo
from heimdall.modules.tenancy.models import Installation, Repo
from heimdall.shared.context import TenantCtx, new_request_id
from heimdall.shared.errors import InstallationNotFound, InstallationSuspended
from heimdall.shared.ids import InstallationId


async def on_installation_event(
    sessions: async_sessionmaker[AsyncSession], event: InstallationEvent
) -> None:
    """Create or update tenant records from a verified GitHub installation event.

    Args:
        sessions: Tenant database session factory.
        event: Parsed installation or installation-repositories event.
    """
    installation_id = InstallationId(event.installation_id)
    async with unit_of_work(sessions) as session:
        installation = await _installation_record(session, installation_id, lock=True)
        if installation is None:
            installation = Installation(
                installation_id=event.installation_id,
                account_login=event.account_login,
                account_type=event.account_type,
                suspended=event.action in {"suspend", "deleted"},
            )
            session.add(installation)
        else:
            installation.account_login = event.account_login
            installation.account_type = event.account_type
            if event.action in {"suspend", "deleted"}:
                installation.suspended = True
            elif event.action in {"created", "unsuspend"}:
                installation.suspended = False
        for repository in event.repositories:
            await _upsert_repo(session, installation_id, repository)
        if event.event == "installation_repositories" and event.action == "removed":
            await _disable_repos(session, installation_id, event.removed_repo_ids)
        if event.action == "deleted":
            await _set_repos_enabled(session, installation_id, False)


async def get_tenant(
    sessions: async_sessionmaker[AsyncSession], installation_id: InstallationId
) -> TenantCtx:
    """Return request context for an active installation.

    Args:
        sessions: Tenant database session factory.
        installation_id: GitHub App installation that owns the request.

    Returns:
        A new tenant request context.

    Raises:
        InstallationNotFound: The installation is not registered.
        InstallationSuspended: The installation is suspended or deleted.
    """
    async with unit_of_work(sessions) as session:
        installation = await _installation_record(session, installation_id)
        if installation is None:
            raise InstallationNotFound(f"installation {installation_id} is not registered")
        if installation.suspended:
            raise InstallationSuspended(f"installation {installation_id} is suspended")
    return TenantCtx(installation_id=installation_id, request_id=new_request_id())


async def _installation_record(
    session: AsyncSession, installation_id: InstallationId, *, lock: bool = False
) -> Installation | None:
    """Select one installation by its tenant key."""
    statement = select_for_tenant(Installation, installation_id).where(
        Installation.installation_id == installation_id
    )
    if lock:
        statement = statement.with_for_update()
    result = await session.exec(statement)
    return result.first()


async def _upsert_repo(
    session: AsyncSession, installation_id: InstallationId, repository: InstallationRepo
) -> None:
    """Enable a repository without replacing cached config or room mapping."""
    result = await session.exec(
        select_for_tenant(Repo, installation_id).where(Repo.repo_id == repository.repo_id)
    )
    record = result.first()
    if record is None:
        session.add(
            Repo(
                repo_id=repository.repo_id,
                installation_id=installation_id,
                full_name=repository.full_name,
                config={},
                enabled=True,
            )
        )
        return
    record.full_name = repository.full_name
    record.enabled = True


async def _disable_repos(
    session: AsyncSession, installation_id: InstallationId, repo_ids: tuple[int, ...]
) -> None:
    """Disable removed repositories while retaining their PR history."""
    if not repo_ids:
        return
    result = await session.exec(
        select_for_tenant(Repo, installation_id).where(col(Repo.repo_id).in_(repo_ids))
    )
    for record in result:
        record.enabled = False


async def _set_repos_enabled(
    session: AsyncSession, installation_id: InstallationId, enabled: bool
) -> None:
    """Apply installation suspension state to its repositories."""
    result = await session.exec(select_for_tenant(Repo, installation_id))
    for record in result:
        record.enabled = enabled
