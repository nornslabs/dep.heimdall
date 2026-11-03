import pytest
from sqlalchemy import text

from heimdall.adapters.db.engine import create_engine

pytestmark = pytest.mark.integration


async def test_postgres_is_reachable(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT 1")) == 1
    finally:
        await engine.dispose()
