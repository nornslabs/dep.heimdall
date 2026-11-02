from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, inspect
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parents[3] / "alembic.ini"


def _upgrade_head(connection: Connection) -> list[str]:
    config = Config(str(ALEMBIC_INI))
    config.attributes["connection"] = connection
    command.upgrade(config, "head")
    return inspect(connection).get_table_names()


async def test_alembic_upgrade_head_runs_in_given_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        tables = await conn.run_sync(_upgrade_head)

    assert "alembic_version" in tables
