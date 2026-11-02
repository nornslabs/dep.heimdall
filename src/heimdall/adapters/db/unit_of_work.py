"""One database transaction per use case (AGENTS.md rule 5)."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession


@asynccontextmanager
async def unit_of_work(sessions: async_sessionmaker[AsyncSession]) -> AsyncGenerator[AsyncSession]:
    """Open a session inside one transaction that commits all or nothing.

    The transaction commits when the block exits normally and rolls back if it raises,
    so a state change, its queued job and its outbox row land together or not at all.

    Args:
        sessions: Factory from ``make_session_factory``.

    Yields:
        A session already inside a transaction. Do not call ``commit()`` on it.
    """
    async with sessions() as session, session.begin():
        yield session
