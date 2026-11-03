"""Installation lifecycle use cases."""

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.tenancy._installation_events import InstallationEvent
from heimdall.modules.tenancy._installation_store import apply_installation_event
from heimdall.modules.tenancy.models import Installation
from heimdall.shared.context import TenantCtx, new_request_id
from heimdall.shared.errors import InstallationNotFound, InstallationSuspended
from heimdall.shared.ids import InstallationId


async def on_installation_event(
    sessions: async_sessionmaker[AsyncSession], event: InstallationEvent
) -> None:
    """Persist one parsed installation event and its repository changes atomically.

    Args:
        sessions: Tenant database session factory.
        event: Parsed installation lifecycle change.
    """
    async with unit_of_work(sessions) as session:
        await apply_installation_event(session, event)


async def get_tenant(
    sessions: async_sessionmaker[AsyncSession],
    installation_id: InstallationId,
    request_id: str | None = None,
) -> TenantCtx:
    """Create a request context only for an active installation.

    Args:
        sessions: Tenant database session factory.
        installation_id: GitHub App installation that owns the request.
        request_id: Optional correlation id; a new id is generated when omitted.

    Returns:
        Tenant context scoped to the active installation.

    Raises:
        InstallationNotFound: The installation is not registered.
        InstallationSuspended: The installation is suspended or deleted.
    """
    async with unit_of_work(sessions) as session:
        installation = await session.get(Installation, installation_id)
    if installation is None:
        raise InstallationNotFound(f"installation {installation_id} is not registered")
    if installation.suspended:
        raise InstallationSuspended(f"installation {installation_id} suspended")
    return TenantCtx(
        installation_id=installation_id,
        request_id=request_id or new_request_id(),
    )
