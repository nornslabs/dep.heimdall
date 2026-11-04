"""Procrastinate worker for review jobs: wakes Loki once a PR reaches ``checking``."""

import asyncio
import uuid

from procrastinate import App
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from agents._job_context import load_pr_job_context
from agents._lifecycle import warn_if_shared_model
from agents._mcp_tools import LokiMcpTools
from agents._runtime import ChatModel
from agents.heimdall.config import load_ai_config as load_heimdall_config
from agents.huginn.config import load_ai_config as load_huginn_config
from agents.loki.agent import run_loki
from agents.loki.config import AGENT_NAME, load_ai_config
from apps.mcp.main import create_server
from heimdall.adapters.ai import AiAdapter, AiConfig
from heimdall.adapters.db.engine import create_engine, make_session_factory
from heimdall.adapters.github.auth import GitHubAppClients, load_private_key
from heimdall.adapters.github.reader import GitHubApiReader
from heimdall.adapters.queue.app import create_queue_app
from heimdall.adapters.queue.enqueue import LOKI_QUEUE
from heimdall.shared.context import TenantCtx, new_request_id
from heimdall.shared.errors import ConfigInvalid
from heimdall.shared.ids import InstallationId
from heimdall.shared.logging import get_logger

log = get_logger(__name__)


def _other_agent_configs() -> dict[str, AiConfig]:
    """Load the other agents' model settings for the independence check, skipping unset ones."""
    configs: dict[str, AiConfig] = {}
    for name, load in (("huginn", load_huginn_config), ("heimdall", load_heimdall_config)):
        try:
            configs[name] = load()
        except ConfigInvalid:
            continue
    return configs


def register_loki_task(
    queue_app: App,
    sessions: async_sessionmaker[AsyncSession],
    reader: GitHubApiReader,
    model: ChatModel,
) -> None:
    """Register the review job that runs Loki once per PR head.

    Args:
        queue_app: Procrastinate app used by Loki's worker.
        sessions: Tenant-scoped database sessions.
        reader: Authenticated read-only GitHub adapter.
        model: Loki's model; the e2e replay passes a scripted one.
    """

    @queue_app.task(name="loki.run_review")
    async def run_review_task(installation_id: int, pr_id: str, head_sha: str) -> None:
        """Review one PR head; saving the findings tells triage Loki is done."""
        ctx = TenantCtx(InstallationId(installation_id), new_request_id())
        job = await load_pr_job_context(sessions, ctx, uuid.UUID(pr_id), head_sha)
        if job is None:
            log.info("loki_job_stale", installation_id=installation_id, pr_id=pr_id)
            return
        tools = LokiMcpTools(create_server(sessions, reader, AGENT_NAME))
        await run_loki(tools, model, installation_id, job.snapshot.id, job.repo_full_name)


async def run_worker() -> None:
    """Consume Loki's review jobs from the shared Postgres queue."""
    from heimdall.shared.logging import configure_logging
    from heimdall.shared.settings import get_settings

    settings = get_settings()
    configure_logging(settings.log_level)
    config = load_ai_config()
    warn_if_shared_model(config, _other_agent_configs())
    ai = AiAdapter(config)
    database_url = settings.database_url.get_secret_value()
    engine = create_engine(database_url)
    clients = GitHubAppClients(
        settings.github_app_id, load_private_key(settings.github_private_key_path)
    )
    queue_app = create_queue_app(database_url)
    register_loki_task(queue_app, make_session_factory(engine), GitHubApiReader(clients), ai)
    try:
        async with queue_app.open_async():
            await queue_app.run_worker_async(queues=[LOKI_QUEUE])
    finally:
        await engine.dispose()
        await ai.aclose()


def main() -> None:
    """Start the dedicated Loki queue consumer."""
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
