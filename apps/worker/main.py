"""Background worker: runs queued jobs and the outbox sender loop side by side."""

import asyncio
from collections.abc import Mapping

from procrastinate import App

from heimdall.adapters.db.engine import create_engine, make_session_factory
from heimdall.adapters.db.outbox.sender import ChannelSender, run_outbox_loop
from heimdall.adapters.queue.app import create_queue_app
from heimdall.shared.logging import configure_logging, get_logger
from heimdall.shared.settings import get_settings

# Channel senders arrive with their issues: github in HEI-65 (approval), switch in HEI-53.
# Until then messages stay pending rather than failing.
OUTBOX_SENDERS: Mapping[str, ChannelSender] = {}

log = get_logger(__name__)


async def _run_jobs_then_stop_outbox(queue_app: App, stop: asyncio.Event) -> None:
    """Run the procrastinate worker; when it stops (SIGTERM, SIGINT), stop the outbox too."""
    try:
        await queue_app.run_worker_async()
    finally:
        stop.set()


async def run_worker() -> None:
    """Wire settings, database and queue, then run jobs and the outbox until told to stop."""
    settings = get_settings()
    configure_logging(settings.log_level)
    database_url = settings.database_url.get_secret_value()
    engine = create_engine(database_url)
    queue_app = create_queue_app(database_url)
    stop = asyncio.Event()
    log.info("worker_started", outbox_channels=sorted(OUTBOX_SENDERS))
    try:
        async with queue_app.open_async(), asyncio.TaskGroup() as tasks:
            tasks.create_task(_run_jobs_then_stop_outbox(queue_app, stop))
            tasks.create_task(run_outbox_loop(make_session_factory(engine), OUTBOX_SENDERS, stop))
    finally:
        await engine.dispose()
        log.info("worker_stopped")


def main() -> None:
    """Run the worker process."""
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
