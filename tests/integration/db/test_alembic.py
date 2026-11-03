import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from heimdall.adapters.db.metadata import import_all_models, include_in_migrations, metadata

pytestmark = pytest.mark.integration

TEST_TABLE_PREFIXES = ("test_", "unit_test_")


def _is_test_table(diff: object) -> bool:
    text = repr(diff)
    return any(f"'{prefix}" in text or f"Table('{prefix}" in text for prefix in TEST_TABLE_PREFIXES)


def _schema_drift(connection: Connection) -> list[object]:
    import_all_models()
    context = MigrationContext.configure(
        connection, opts={"compare_type": True, "include_name": include_in_migrations}
    )
    return [diff for diff in compare_metadata(context, metadata) if not _is_test_table(diff)]


async def test_migrations_match_the_models(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        drift = await conn.run_sync(_schema_drift)

    assert drift == []


async def test_migrations_record_their_version(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        version = await conn.exec_driver_sql("SELECT version_num FROM alembic_version")

        assert version.scalar_one()


async def test_migrations_install_the_job_queue(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        result = await conn.exec_driver_sql("SELECT to_regclass('procrastinate_jobs') IS NOT NULL")

        assert result.scalar_one() is True
