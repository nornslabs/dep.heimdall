"""A fresh Postgres schema per test session, so tests never touch real tables."""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.engine import create_engine, make_session_factory
from heimdall.adapters.db.metadata import import_all_models, metadata

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is not set")
    return url


async def _run_admin_sql(database_url: str, sql: str) -> None:
    engine = create_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(sql))
    finally:
        await engine.dispose()


async def _create_tables(database_url: str, schema: str) -> None:
    import_all_models()
    engine = create_engine(database_url, schema=schema)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def db_schema(database_url: str) -> Iterator[str]:
    schema = f"test_{uuid.uuid4().hex[:12]}"
    asyncio.run(_run_admin_sql(database_url, f'CREATE SCHEMA "{schema}"'))
    asyncio.run(_create_tables(database_url, schema))
    yield schema
    asyncio.run(_run_admin_sql(database_url, f'DROP SCHEMA "{schema}" CASCADE'))


@pytest.fixture
async def engine(database_url: str, db_schema: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(database_url, schema=db_schema)
    yield engine
    await engine.dispose()


@pytest.fixture
def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return make_session_factory(engine)
