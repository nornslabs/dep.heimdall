import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration

INSERT_ENTRY = (
    "INSERT INTO audit_log (installation_id, actor, action, subject, payload) "
    "VALUES (901, 'policy:auto_suggest', 'approved', 'draft:1', '{}') RETURNING id"
)


async def _insert_entry(engine: AsyncEngine) -> int:
    async with engine.begin() as conn:
        result = await conn.exec_driver_sql(INSERT_ENTRY)
        return result.scalar_one()


async def test_audit_log_accepts_inserts(engine: AsyncEngine) -> None:
    assert await _insert_entry(engine) > 0


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_log SET actor = 'someone-else' WHERE id = {id}",
        "DELETE FROM audit_log WHERE id = {id}",
        "TRUNCATE audit_log",
    ],
)
async def test_audit_log_rejects_changes(engine: AsyncEngine, statement: str) -> None:
    entry_id = await _insert_entry(engine)

    with pytest.raises(DBAPIError, match="insert-only"):
        async with engine.begin() as conn:
            await conn.exec_driver_sql(statement.format(id=entry_id))
