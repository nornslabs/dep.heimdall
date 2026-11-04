"""Procrastinate worker for card jobs: wakes Heimdall once both checks finished on a head."""

import asyncio
import uuid

from procrastinate import App
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from agents._job_context import load_pr_job_context
from agents._mcp_tools import HeimdallMcpTools
from agents.heimdall.agent import build_review_card
from agents.heimdall.config import AGENT_NAME, load_ai_config
from apps.mcp.main import create_server
from heimdall.adapters.ai import AiAdapter
from heimdall.adapters.db.engine import create_engine, make_session_factory
from heimdall.adapters.db.outbox.writer import write_outbox
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.adapters.github.auth import GitHubAppClients, load_private_key
from heimdall.adapters.github.reader import GitHubApiReader
from heimdall.adapters.queue.app import create_queue_app
from heimdall.adapters.queue.enqueue import HEIMDALL_QUEUE
from heimdall.modules.repro.api import latest_run_id
from heimdall.shared.context import TenantCtx, new_request_id
from heimdall.shared.github import RepoRef
from heimdall.shared.ids import InstallationId
from heimdall.shared.logging import get_logger

log = get_logger(__name__)

# A card exists only after intake passed (the state machine guards it), so this is always true.
RULES_SUMMARY = "All intake rules passed."
SUGGESTED_ACTION = "Review the card, then approve the comment to post it."


def register_heimdall_task(
    queue_app: App,
    sessions: async_sessionmaker[AsyncSession],
    reader: GitHubApiReader,
) -> None:
    """Register the card job that saves the review card and queues its room post.

    Args:
        queue_app: Procrastinate app used by Heimdall's worker.
        sessions: Tenant-scoped database sessions.
        reader: Authenticated read-only GitHub adapter, used for the PR title.
    """

    @queue_app.task(name="heimdall.build_card")
    async def build_card_task(installation_id: int, pr_id: str, head_sha: str) -> None:
        """Save the card and draft, then queue the approval message for the PR's room."""
        tenant_id = InstallationId(installation_id)
        ctx = TenantCtx(tenant_id, new_request_id())
        job = await load_pr_job_context(sessions, ctx, uuid.UUID(pr_id), head_sha)
        if job is None:
            log.info("heimdall_job_stale", installation_id=installation_id, pr_id=pr_id)
            return
        snapshot = job.snapshot
        info = await reader.get_pull_request(
            RepoRef(tenant_id, job.repo_full_name), snapshot.number
        )
        tools = HeimdallMcpTools(create_server(sessions, reader, AGENT_NAME))
        card = await build_review_card(
            tools,
            installation_id,
            snapshot.id,
            snapshot.number,
            job.repo_full_name,
            info.title,
            head_sha,
            RULES_SUMMARY,
            SUGGESTED_ACTION,
            await latest_run_id(sessions, ctx, snapshot.id, head_sha),
        )
        if not job.room_id:
            log.warning("card_not_posted_no_room", installation_id=installation_id, pr_id=pr_id)
            return
        async with unit_of_work(sessions) as session:
            await write_outbox(
                session,
                installation_id=tenant_id,
                channel="switch",
                kind="room.message",
                payload={"channel_id": job.room_id, "message": card.approval_message},
                idempotency_key=f"heimdall:{installation_id}:{pr_id}:{head_sha}:card",
            )


async def run_worker() -> None:
    """Consume Heimdall's card jobs from the shared Postgres queue."""
    from heimdall.shared.logging import configure_logging
    from heimdall.shared.settings import get_settings

    settings = get_settings()
    configure_logging(settings.log_level)
    # Built at startup so a bad LLM_HEIMDALL_* / LLM_DEFAULT_* config fails before any job runs.
    ai = AiAdapter(load_ai_config())
    database_url = settings.database_url.get_secret_value()
    engine = create_engine(database_url)
    clients = GitHubAppClients(
        settings.github_app_id, load_private_key(settings.github_private_key_path)
    )
    queue_app = create_queue_app(database_url)
    register_heimdall_task(queue_app, make_session_factory(engine), GitHubApiReader(clients))
    try:
        async with queue_app.open_async():
            await queue_app.run_worker_async(queues=[HEIMDALL_QUEUE])
    finally:
        await engine.dispose()
        await ai.aclose()


def main() -> None:
    """Start the dedicated Heimdall queue consumer."""
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
