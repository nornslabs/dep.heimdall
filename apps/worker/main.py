"""Background worker: runs queued jobs and the outbox sender loop side by side."""

import asyncio
from contextlib import AsyncExitStack

from procrastinate import App

from apps.worker.tasks import (
    register_actions_result_task,
    register_installation_task,
    register_intake_task,
    register_repro_timeout_task,
    register_triage_task,
)
from heimdall.adapters.db.engine import create_engine, make_session_factory
from heimdall.adapters.db.outbox.sender import ChannelSender, run_outbox_loop
from heimdall.adapters.github.actions import GitHubActionsResults
from heimdall.adapters.github.auth import GitHubAppClients, load_private_key
from heimdall.adapters.github.reader import GitHubApiReader
from heimdall.adapters.github.sender import GitHubSender
from heimdall.adapters.queue.app import create_queue_app
from heimdall.adapters.queue.enqueue import DEFAULT_QUEUE
from heimdall.adapters.switch import SwitchRoutingSender, SwitchSender
from heimdall.adapters.switch.agent.credentials import switch_api_url
from heimdall.adapters.switch.agent.posters import build_agent_posters
from heimdall.shared.logging import configure_logging, get_logger
from heimdall.shared.settings import get_settings

log = get_logger(__name__)


async def _run_jobs_then_stop_outbox(queue_app: App, stop: asyncio.Event) -> None:
    """Run the procrastinate worker; when it stops (SIGTERM, SIGINT), stop the outbox too."""
    try:
        # Huginn's jobs sit on their own queue and run in the huginn service.
        await queue_app.run_worker_async(queues=[DEFAULT_QUEUE])
    finally:
        stop.set()


async def run_worker() -> None:
    """Wire settings, database and queue, then run jobs and the outbox until told to stop."""
    settings = get_settings()
    configure_logging(settings.log_level)
    senders: dict[str, ChannelSender] = {}
    # Built before anything is opened so a half-set agent key fails the start, not a post.
    agent_posters = build_agent_posters(switch_api_url())
    bot: SwitchSender | None = None
    if settings.switch_url and settings.switch_token:
        bot = SwitchSender(settings.switch_url, settings.switch_token.get_secret_value())
    if bot is not None or agent_posters:
        senders["switch"] = SwitchRoutingSender(bot, agent_posters)
    database_url = settings.database_url.get_secret_value()
    engine = create_engine(database_url)
    queue_app = create_queue_app(database_url)
    sessions = make_session_factory(engine)
    github_clients = GitHubAppClients(
        settings.github_app_id,
        load_private_key(settings.github_private_key_path),
    )
    senders["github"] = GitHubSender(sessions, github_clients)
    register_intake_task(queue_app, sessions, GitHubApiReader(github_clients))
    register_triage_task(queue_app, sessions)
    register_repro_timeout_task(queue_app, sessions)
    register_installation_task(queue_app, sessions, settings.default_switch_room_id)
    register_actions_result_task(queue_app, sessions, GitHubActionsResults(github_clients))
    stop = asyncio.Event()
    log.info("worker_started", outbox_channels=sorted(senders))
    try:
        async with queue_app.open_async(), asyncio.TaskGroup() as tasks:
            tasks.create_task(_run_jobs_then_stop_outbox(queue_app, stop))
            tasks.create_task(run_outbox_loop(sessions, senders, stop))
    finally:
        # One failing close must not leave the others open.
        async with AsyncExitStack() as closers:
            closers.push_async_callback(engine.dispose)
            for poster in agent_posters.values():
                closers.push_async_callback(poster.aclose)
            if bot is not None:
                closers.push_async_callback(bot.aclose)
        log.info("worker_stopped")


def main() -> None:
    """Run the worker process."""
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
