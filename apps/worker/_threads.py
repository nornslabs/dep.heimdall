"""The worker's PR thread store: triage's thread id, reached by plain ids from outbox rows."""

from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.modules.triage.api import get_switch_thread_id, save_switch_thread_id
from heimdall.shared.context import TenantCtx, new_request_id
from heimdall.shared.ids import InstallationId


class SessionThreadStore:
    """Reads and saves each PR's Switch thread id for the outbox's routing sender."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Keep the tenant database session factory the store reads and writes through."""
        self._sessions = sessions

    async def get(self, installation_id: int, pr_id: str) -> str | None:
        """Return the PR's thread id, or None when its thread is not open yet."""
        return await get_switch_thread_id(self._sessions, _ctx(installation_id), UUID(pr_id))

    async def save(self, installation_id: int, pr_id: str, thread_id: str) -> None:
        """Remember ``thread_id`` for the PR; the first one saved is kept."""
        await save_switch_thread_id(self._sessions, _ctx(installation_id), UUID(pr_id), thread_id)


def _ctx(installation_id: int) -> TenantCtx:
    """Build the tenant context for one outbox row."""
    return TenantCtx(InstallationId(installation_id), new_request_id())
