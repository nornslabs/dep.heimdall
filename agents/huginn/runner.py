"""Procrastinate worker for ready-for-reproduction Huginn jobs."""

import uuid
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from procrastinate import App
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from agents._job_context import PrJobContext, load_pr_job_context
from agents._mcp_tools import McpToolClient, mcp_agent_tools
from agents._mention_ports import MentionDeps
from agents._mention_task import register_mention_task
from agents._presence_runner import run_with_presence
from agents._runtime import ChatModel, Completed, ToolBox
from agents._skills import LOAD_SKILL_TOOL, SkillSet, load_skills
from agents.huginn._message import render_verdict
from agents.huginn.agent import run_huginn
from agents.huginn.allowed_tools import ALLOWED_TOOLS
from agents.huginn.config import AGENT_NAME, REQUIRED_SKILLS, load_ai_config
from agents.huginn.context import build_messages, build_repro_brief
from agents.huginn.guardrails import ReproGuard, guard_start_repro
from agents.huginn.mention import MENTION_AGENT, guard_act_tools
from agents.huginn.schemas import HuginnVerdict, submit_verdict_tool
from apps.mcp.main import create_server
from heimdall.adapters.ai import AiAdapter
from heimdall.adapters.db.engine import create_engine, make_session_factory
from heimdall.adapters.db.outbox.writer import write_outbox
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.adapters.github.auth import GitHubAppClients, load_private_key
from heimdall.adapters.github.reader import GitHubApiReader
from heimdall.adapters.queue.app import create_queue_app
from heimdall.adapters.queue.enqueue import HUGINN_QUEUE
from heimdall.modules.tenancy.api import load_config
from heimdall.modules.triage.api import get_pr_snapshot
from heimdall.shared.agents import AgentName
from heimdall.shared.context import TenantCtx, new_request_id
from heimdall.shared.github import RepoRef
from heimdall.shared.ids import InstallationId
from heimdall.shared.logging import get_logger

PROMPT_PATH = Path(__file__).with_name("prompt.md")

log = get_logger(__name__)


def register_huginn_task(
    queue_app: App,
    sessions: async_sessionmaker[AsyncSession],
    reader: GitHubApiReader,
    model: ChatModel,
) -> None:
    """Register the ready-for-reproduction job that lets Huginn test one PR head.

    Args:
        queue_app: Procrastinate app used by the Huginn worker.
        sessions: Tenant-scoped database sessions.
        reader: Authenticated read-only GitHub adapter.
        model: Huginn's model; the e2e replay passes a scripted one.
    """
    register_mention_task(
        queue_app, MentionDeps(MENTION_AGENT, sessions, reader, lambda: model, guard_act_tools)
    )

    @queue_app.task(name="huginn.run_repro")
    async def run_repro_task(
        installation_id: int,
        pr_id: str,
        head_sha: str,
        test_cmd: str,
    ) -> None:
        """Let Huginn test one head, then queue its verdict message for the room."""
        tenant_id = InstallationId(installation_id)
        ctx = TenantCtx(tenant_id, new_request_id())
        pr_uuid = uuid.UUID(pr_id)
        job = await load_pr_job_context(sessions, ctx, pr_uuid, head_sha)
        if job is None or not job.agent_room_id:
            log.info("huginn_job_dropped", installation_id=installation_id, pr_id=pr_id)
            return
        verdict = await _verdict_for(sessions, reader, model, ctx, job, installation_id, test_cmd)
        if verdict is None:
            return
        await _queue_verdict(sessions, ctx, pr_uuid, head_sha, job.agent_room_id, verdict)


async def _verdict_for(
    sessions: async_sessionmaker[AsyncSession],
    reader: GitHubApiReader,
    model: ChatModel,
    ctx: TenantCtx,
    job: PrJobContext,
    installation_id: int,
    test_cmd: str,
) -> HuginnVerdict | None:
    """Run Huginn on one PR head and return its checked verdict, or None when it did not finish."""
    snapshot = job.snapshot
    repo = RepoRef(ctx.installation_id, job.repo_full_name)
    config = await load_config(sessions, reader, ctx, repo, snapshot.head_sha)
    info = await reader.get_pull_request(repo, snapshot.number)
    changed = await reader.list_changed_files(repo, snapshot.number)
    skills = load_skills(AgentName.HUGINN, ALLOWED_TOOLS, REQUIRED_SKILLS)
    server = create_server(sessions, reader, AGENT_NAME)
    brief = build_repro_brief(
        installation_id=installation_id,
        pr_id=str(snapshot.id),
        repo_full_name=job.repo_full_name,
        number=snapshot.number,
        title=info.title,
        body=info.body,
        head_sha=snapshot.head_sha,
        test_cmd=test_cmd,
        changed_paths=[file.path for file in changed],
    )
    outcome = await run_huginn(
        ai=model,
        toolbox=await _toolbox(server, skills, config.repro.allowed_runners),
        submit=submit_verdict_tool(McpToolClient(server), installation_id, snapshot.head_sha),
        head_sha=snapshot.head_sha,
        read_current_head=lambda: _current_head(sessions, ctx, snapshot.id),
        messages=build_messages(brief, _instructions(), skills.prompt_index()),
    )
    if not isinstance(outcome, Completed):
        log.info("huginn_did_not_submit", pr_id=str(snapshot.id), reason=type(outcome).__name__)
        return None
    return outcome.payload


async def _toolbox(
    server: MCPServer, skills: SkillSet, allowed_runners: tuple[str, ...]
) -> ToolBox:
    """Build Huginn's ToolBox, with the repro guard wrapped around ``start_repro``."""
    guard = ReproGuard(allowed_runners)
    tools = await mcp_agent_tools(server, ALLOWED_TOOLS)
    guarded = tuple(
        guard_start_repro(guard, tool) if tool.spec.name == "start_repro" else tool
        for tool in tools
    )
    return ToolBox(ALLOWED_TOOLS | {LOAD_SKILL_TOOL}, (*guarded, skills.tool()))


async def _queue_verdict(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    pr_id: uuid.UUID,
    head_sha: str,
    room_id: str,
    verdict: HuginnVerdict,
) -> None:
    """Queue the rendered verdict for the PR's room, once per head."""
    async with unit_of_work(sessions) as session:
        await write_outbox(
            session,
            installation_id=ctx.installation_id,
            channel="switch",
            kind="room.message",
            payload={
                "agent": AgentName.HUGINN.value,
                "channel_id": room_id,
                "message": render_verdict(verdict),
                "pr_id": str(pr_id),
            },
            idempotency_key=f"huginn:{ctx.installation_id}:{pr_id}:{head_sha}:verdict",
        )


async def _current_head(
    sessions: async_sessionmaker[AsyncSession], ctx: TenantCtx, pr_id: uuid.UUID
) -> str:
    """Read the PR's head again, so a verdict for a head that moved is dropped."""
    async with unit_of_work(sessions) as session:
        snapshot = await get_pr_snapshot(session, ctx, pr_id)
    if snapshot is None:
        raise ValueError(f"pull request {pr_id} is not registered for this installation")
    return snapshot.head_sha


def _instructions() -> str:
    """Return Huginn's role prompt, read from ``prompt.md`` beside this module."""
    return PROMPT_PATH.read_text(encoding="utf-8")


async def run_worker() -> None:
    """Consume Huginn jobs from the shared Postgres queue."""
    from heimdall.shared.logging import configure_logging
    from heimdall.shared.settings import get_settings

    settings = get_settings()
    configure_logging(settings.log_level)
    # Validated at startup so a broken playbook fails before any job runs.
    load_skills(AgentName.HUGINN, ALLOWED_TOOLS, REQUIRED_SKILLS)
    # Built at startup so a bad LLM_HUGINN_* / LLM_DEFAULT_* config fails before any job runs.
    ai = AiAdapter(load_ai_config())
    database_url = settings.database_url.get_secret_value()
    engine = create_engine(database_url)
    clients = GitHubAppClients(
        settings.github_app_id, load_private_key(settings.github_private_key_path)
    )
    queue_app = create_queue_app(database_url)
    sessions = make_session_factory(engine)
    register_huginn_task(queue_app, sessions, GitHubApiReader(clients), ai)
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
    import asyncio

    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
