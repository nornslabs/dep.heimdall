import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.outbox.writer import write_outbox
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.adapters.queue.enqueue import enqueue
from heimdall.shared.ids import InstallationId

pytestmark = pytest.mark.integration


class BoomError(Exception):
    pass


async def _count(engine: AsyncEngine, sql: str, marker: str) -> int:
    async with engine.connect() as conn:
        result = await conn.execute(text(sql), {"marker": marker})
        return result.scalar_one()


async def _jobs_for(engine: AsyncEngine, marker: str) -> int:
    sql = "SELECT count(*) FROM procrastinate_jobs WHERE args->>'marker' = :marker"
    return await _count(engine, sql, marker)


async def _outbox_rows_for(engine: AsyncEngine, marker: str) -> int:
    return await _count(
        engine, "SELECT count(*) FROM outbox WHERE idempotency_key = :marker", marker
    )


async def _enqueue_and_write(session: AsyncSession, marker: str) -> int:
    job_id = await enqueue(session, "triage.handle_event", {"installation_id": 1, "marker": marker})
    await write_outbox(
        session,
        installation_id=InstallationId(1),
        channel="switch",
        kind="room.message",
        payload={"text": "hello"},
        idempotency_key=marker,
    )
    return job_id


async def test_rollback_leaves_no_job_and_no_outbox_row(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    with pytest.raises(BoomError):
        async with unit_of_work(sessions) as session:
            await _enqueue_and_write(session, "rollback-1")
            raise BoomError

    assert await _jobs_for(engine, "rollback-1") == 0
    assert await _outbox_rows_for(engine, "rollback-1") == 0


async def test_commit_keeps_job_and_outbox_row_together(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with unit_of_work(sessions) as session:
        job_id = await _enqueue_and_write(session, "commit-1")

    assert job_id > 0
    assert await _jobs_for(engine, "commit-1") == 1
    assert await _outbox_rows_for(engine, "commit-1") == 1


async def test_writing_the_same_idempotency_key_twice_keeps_one_row(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    for _ in range(2):
        async with unit_of_work(sessions) as session:
            await _enqueue_and_write(session, "twice-1")

    assert await _outbox_rows_for(engine, "twice-1") == 1
