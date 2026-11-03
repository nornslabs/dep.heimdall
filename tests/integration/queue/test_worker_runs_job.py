import pytest
from procrastinate import App
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.adapters.queue.app import create_queue_app
from heimdall.adapters.queue.enqueue import DEFAULT_QUEUE, HUGINN_QUEUE, enqueue

pytestmark = pytest.mark.integration


def _app_with_recording_task(database_url: str, db_schema: str, seen: list[str]) -> App:
    app = create_queue_app(database_url, schema=db_schema)

    @app.task(name="test.record")
    async def record(installation_id: int, marker: str) -> None:  # pyright: ignore[reportUnusedFunction]
        seen.append(f"{installation_id}:{marker}")

    return app


async def test_procrastinate_worker_runs_a_job_enqueued_in_our_transaction(
    database_url: str, db_schema: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    seen: list[str] = []
    app = _app_with_recording_task(database_url, db_schema, seen)
    async with unit_of_work(sessions) as session:
        await enqueue(session, "test.record", {"installation_id": 7, "marker": "ran"})

    async with app.open_async():
        await app.run_worker_async(queues=["default"], wait=False)

    assert seen == ["7:ran"]


async def test_huginn_worker_leaves_default_queue_jobs_for_the_main_worker(
    database_url: str, db_schema: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    seen: list[str] = []
    app = _app_with_recording_task(database_url, db_schema, seen)
    async with unit_of_work(sessions) as session:
        await enqueue(session, "test.record", {"installation_id": 7, "marker": "main"})

    async with app.open_async():
        await app.run_worker_async(queues=[HUGINN_QUEUE], wait=False)
        assert seen == []
        await app.run_worker_async(queues=[DEFAULT_QUEUE], wait=False)

    assert seen == ["7:main"]


async def test_main_worker_leaves_huginn_queue_jobs_for_huginn(
    database_url: str, db_schema: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    seen: list[str] = []
    app = _app_with_recording_task(database_url, db_schema, seen)
    async with unit_of_work(sessions) as session:
        await enqueue(
            session, "test.record", {"installation_id": 7, "marker": "repro"}, queue=HUGINN_QUEUE
        )

    async with app.open_async():
        await app.run_worker_async(queues=[DEFAULT_QUEUE], wait=False)
        assert seen == []
        await app.run_worker_async(queues=[HUGINN_QUEUE], wait=False)

    assert seen == ["7:repro"]
