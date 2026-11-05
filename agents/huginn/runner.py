"""Procrastinate worker for ready-for-reproduction Huginn jobs."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable

from procrastinate import App
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from agents._job_context import load_agent_room_id
from agents._mention import register_mention_task
from agents._presence_runner import run_with_presence
from agents.huginn.agent import run_huginn
from agents.huginn.config import load_ai_config
from heimdall.adapters.ai import AiAdapter
from heimdall.adapters.db.engine import create_engine, make_session_factory
from heimdall.adapters.db.outbox.writer import write_outbox
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.adapters.queue.app import create_queue_app
from heimdall.adapters.queue.enqueue import HUGINN_QUEUE
from heimdall.modules.repro.api import get_run, start_run
from heimdall.shared.agents import AgentName
from heimdall.shared.context import TenantCtx, new_request_id
from heimdall.shared.ids import InstallationId


class _DatabaseHuginnTools:
    """Bind Huginn's allowlisted tools to tenant-scoped repro use cases."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], ctx: TenantCtx) -> None:
        self._sessions = sessions
        self._ctx = ctx

    async def start_repro(
        self, installation_id: int, pr_id: str, head_sha: str, test_cmd: str
    ) -> dict[str, str | int]:
        """Start one configured run through the public reproduction API."""
        if installation_id != self._ctx.installation_id:
            raise ValueError("Huginn job tenant does not match its run request")
        run = await start_run(self._sessions, self._ctx, uuid.UUID(pr_id), head_sha, test_cmd)
        return {"run_id": str(run.id), "attempt": run.attempt, "head_sha": run.head_sha}

    async def get_repro(self, installation_id: int, run_id: str) -> dict[str, object]:
        """Read one saved paired result through the public reproduction API."""
        if installation_id != self._ctx.installation_id:
            raise ValueError("Huginn job tenant does not match its run request")
        run = await get_run(self._sessions, self._ctx, uuid.UUID(run_id))
        return {
            "run_id": str(run.id),
            "pr_id": str(run.pr_id),
            "head_sha": run.head_sha,
            "attempt": run.attempt,
            "status": run.status.value,
            "with_fix": run.with_fix,
            "without_fix": run.without_fix,
            "verdict": run.verdict.value if run.verdict is not None else None,
        }


def register_huginn_task(
    queue_app: App,
    sessions: async_sessionmaker[AsyncSession],
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Register the transactional ready-for-reproduction job.

    Args:
        queue_app: Procrastinate app used by the Huginn worker.
        sessions: Tenant-scoped database sessions.
        sleep: Wait between status polls; the e2e replay passes one that advances its fakes.
    """
    register_mention_task(queue_app)

    @queue_app.task(name="huginn.run_repro")
    async def run_repro_task(
        installation_id: int,
        pr_id: str,
        head_sha: str,
        test_cmd: str,
    ) -> None:
        """Run bounded paired attempts and queue one final room report."""
        tenant_id = InstallationId(installation_id)
        ctx = TenantCtx(installation_id=tenant_id, request_id=new_request_id())
        result = await run_huginn(
            _DatabaseHuginnTools(sessions, ctx),
            installation_id,
            uuid.UUID(pr_id),
            head_sha,
            test_cmd,
            sleep=sleep,
        )
        room_id = await load_agent_room_id(sessions, ctx, uuid.UUID(pr_id))
        if not room_id:
            return
        async with unit_of_work(sessions) as session:
            await write_outbox(
                session,
                installation_id=tenant_id,
                channel="switch",
                kind="room.message",
                payload={
                    "agent": AgentName.HUGINN.value,
                    "channel_id": room_id,
                    "message": result.message,
                    "pr_id": pr_id,
                },
                idempotency_key=f"huginn:{installation_id}:{pr_id}:{head_sha}:verdict",
            )


async def run_worker() -> None:
    """Consume Huginn jobs from the shared Postgres queue."""
    from heimdall.shared.logging import configure_logging
    from heimdall.shared.settings import get_settings

    settings = get_settings()
    configure_logging(settings.log_level)
    # Built at startup so a bad LLM_HUGINN_* / LLM_DEFAULT_* config fails before any job runs.
    ai = AiAdapter(load_ai_config())
    database_url = settings.database_url.get_secret_value()
    engine = create_engine(database_url)
    queue_app = create_queue_app(database_url)
    sessions = make_session_factory(engine)
    register_huginn_task(queue_app, sessions)
    try:
        async with queue_app.open_async():
            await run_with_presence(
                AgentName.HUGINN,
                sessions,
                lambda: queue_app.run_worker_async(queues=[HUGINN_QUEUE]),
                default_room_id=settings.default_switch_agent_room_id,
            )
    finally:
        await engine.dispose()
        await ai.aclose()


def main() -> None:
    """Start the dedicated Huginn queue consumer."""
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
