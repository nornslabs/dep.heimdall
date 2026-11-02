"""Builds the async SQLAlchemy engine and session factory for Postgres."""

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

ASYNC_DRIVER = "postgresql+asyncpg"
PLAIN_DRIVERS = frozenset({"postgresql", "postgres"})


def to_async_url(database_url: str) -> str:
    """Point a Postgres URL at the asyncpg driver.

    Settings hold a plain ``postgresql://`` URL so the same value works for psql and CI.

    Args:
        database_url: A ``postgresql://``, ``postgres://`` or ``postgresql+asyncpg://`` URL.

    Returns:
        The same URL with the ``postgresql+asyncpg`` driver.

    Raises:
        ValueError: the URL is not for Postgres, or names a different driver.
    """
    url = make_url(database_url)
    if url.drivername not in PLAIN_DRIVERS | {ASYNC_DRIVER}:
        raise ValueError(f"expected a Postgres URL, got driver {url.drivername!r}")
    return url.set(drivername=ASYNC_DRIVER).render_as_string(hide_password=False)


def create_engine(database_url: str, *, schema: str | None = None) -> AsyncEngine:
    """Create the process-wide async engine.

    Args:
        database_url: Postgres URL from settings.
        schema: Schema to put first on ``search_path``; tests use a throwaway one.

    Returns:
        An AsyncEngine using asyncpg.
    """
    connect_args: dict[str, object] = {}
    if schema is not None:
        connect_args["server_settings"] = {"search_path": schema}
    return create_async_engine(
        to_async_url(database_url), pool_pre_ping=True, connect_args=connect_args
    )


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Make the factory that ``unit_of_work`` opens sessions from.

    Objects stay readable after commit, so a service can return what it just saved.

    Args:
        engine: The engine from ``create_engine``.

    Returns:
        A session factory producing SQLModel AsyncSessions.
    """
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
