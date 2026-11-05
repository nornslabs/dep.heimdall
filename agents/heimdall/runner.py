"""Procrastinate worker for Heimdall jobs: card builds and maintainer commands.

The worker calls ``run_heimdall`` for every job. Card jobs wake on ``pr.ready_for_card``; the
context plan pre-fetches the latest repro result and the accepted findings so the model starts
with a full picture. Command jobs wake on ``maintainer.command`` and are refused before the loop
starts when the sender is not a repository maintainer.
"""

import asyncio
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from mcp.server.mcpserver import MCPServer
from procrastinate import App
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from agents._conversation import MentionAnswer, MentionPorts, Question
from agents._job_context import PrJobContext, load_pr_job_context
from agents._mcp_tools import McpToolClient, mcp_agent_tools
from agents._mention_ports import ApproveByMessage, MentionDeps
from agents._mention_task import register_mention_task
from agents._presence_runner import run_with_presence
from agents._runtime import ChatModel, Completed, ToolBox
from agents._skills import LOAD_SKILL_TOOL, SkillSet, load_skills
from agents._toolbox import AgentTool
from agents.heimdall._approve_message import approve_from_message
from agents.heimdall._intake_job import run_intake_reply
from agents.heimdall._message import render_desk_result
from agents.heimdall.agent import run_heimdall
from agents.heimdall.allowed_tools import ALLOWED_TOOLS
from agents.heimdall.config import AGENT_NAME, REQUIRED_SKILLS, load_ai_config
from agents.heimdall.context import build_desk_brief, build_messages
from agents.heimdall.guardrails import (
    CommandGuard,
    DeskGuard,
    SavedIdTracker,
    guard_create_mechanical_task,
    guard_save_card,
    guard_save_draft,
    submit_desk_result_tool,
)
from agents.heimdall.mention import MENTION_AGENT, guard_act_tools
from agents.heimdall.schemas import DeskResult
from apps.mcp.main import create_server
from heimdall.adapters.ai import AiAdapter
from heimdall.adapters.db.engine import create_engine, make_session_factory
from heimdall.adapters.db.outbox.writer import write_outbox
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.adapters.github.auth import GitHubAppClients, load_private_key
from heimdall.adapters.github.reader import GitHubApiReader
from heimdall.adapters.queue.app import create_queue_app
from heimdall.adapters.queue.enqueue import HEIMDALL_QUEUE
from heimdall.modules.desk.api import (
    format_approval_message,
    get_current_draft_in_transaction,
    get_draft_in_transaction,
    mention_maintainers,
)
from heimdall.modules.repro.api import ReproStatus, Verdict, latest_run_id
from heimdall.modules.tenancy.api import repo_maintainers
from heimdall.modules.triage.api import get_pr_snapshot
from heimdall.shared.agents import AgentName
from heimdall.shared.context import TenantCtx, new_request_id
from heimdall.shared.github import RepoRef
from heimdall.shared.ids import InstallationId
from heimdall.shared.logging import get_logger

PROMPT_PATH = Path(__file__).with_name("prompt.md")

log = get_logger(__name__)


def register_heimdall_task(
    queue_app: App,
    sessions: async_sessionmaker[AsyncSession],
    reader: GitHubApiReader,
    model: ChatModel | None = None,
) -> None:
    """Register the card and command jobs that run Heimdall.

    Args:
        queue_app: Procrastinate app used by Heimdall's worker.
        sessions: Tenant-scoped database sessions.
        reader: Authenticated read-only GitHub adapter, used for PR metadata.
        model: Heimdall's model; defaults to AiAdapter built from config.
    """
    register_mention_task(
        queue_app,
        MentionDeps(
            MENTION_AGENT,
            sessions,
            reader,
            lambda: model or AiAdapter(load_ai_config()),
            guard_act_tools,
            approve_by_message=_approver(sessions, reader),
        ),
    )

    @queue_app.task(name="heimdall.build_card")
    async def build_card_task(installation_id: int, pr_id: str, head_sha: str) -> None:
        """Run Heimdall on a card job, then queue the approval message for the PR's room."""
        active_model = model or AiAdapter(load_ai_config())
        tenant_id = InstallationId(installation_id)
        ctx = TenantCtx(tenant_id, new_request_id())
        pr_uuid = uuid.UUID(pr_id)
        job = await load_pr_job_context(sessions, ctx, pr_uuid, head_sha)
        if job is None:
            log.info("heimdall_job_stale", installation_id=installation_id, pr_id=pr_id)
            return
        result = await _run_for_card(sessions, reader, active_model, ctx, job, installation_id)
        if result is None or not job.agent_room_id:
            if result is not None:
                log.warning("card_not_posted_no_room", installation_id=installation_id, pr_id=pr_id)
            return
        async with unit_of_work(sessions) as session:
            maintainers = await repo_maintainers(session, ctx, job.snapshot.repo_id)
            saved_message = await _saved_message_for(session, ctx, result, job)
            await write_outbox(
                session,
                installation_id=tenant_id,
                channel="switch",
                kind="room.message",
                payload={
                    "agent": AgentName.HEIMDALL.value,
                    "channel_id": job.agent_room_id,
                    "message": mention_maintainers(
                        render_desk_result(result, saved_message), maintainers
                    ),
                    "pr_id": pr_id,
                },
                idempotency_key=f"heimdall:{installation_id}:{pr_id}:{head_sha}:card",
            )

    @queue_app.task(name="heimdall.draft_intake_reply")
    async def draft_intake_reply_task(installation_id: int, pr_id: str, head_sha: str) -> None:
        """Draft the reply to an author whose pull request failed intake."""
        active_model = model or AiAdapter(load_ai_config())
        await run_intake_reply(sessions, active_model, installation_id, pr_id, head_sha)

    @queue_app.task(name="heimdall.run_command")
    async def run_command_task(
        installation_id: int,
        pr_id: str,
        head_sha: str,
        sender: str,
        command_text: str,
    ) -> None:
        """Run Heimdall on a maintainer command."""
        active_model = model or AiAdapter(load_ai_config())
        tenant_id = InstallationId(installation_id)
        ctx = TenantCtx(tenant_id, new_request_id())
        pr_uuid = uuid.UUID(pr_id)
        job = await load_pr_job_context(sessions, ctx, pr_uuid, head_sha)
        if job is None:
            return
        async with unit_of_work(sessions) as session:
            maintainers = await repo_maintainers(session, ctx, job.snapshot.repo_id)
        guard = CommandGuard(maintainers)
        refusal = guard.refusal(sender)
        if refusal is not None:
            log.warning("maintainer_command_refused", sender=sender, reason=refusal)
            return
        result = await _run_for_command(
            sessions, reader, active_model, ctx, job, installation_id, command_text
        )
        if result is None or not job.agent_room_id:
            return
        async with unit_of_work(sessions) as session:
            saved_message = await _saved_message_for(session, ctx, result, job)
            await write_outbox(
                session,
                installation_id=tenant_id,
                channel="switch",
                kind="room.message",
                payload={
                    "agent": AgentName.HEIMDALL.value,
                    "channel_id": job.agent_room_id,
                    "message": render_desk_result(result, saved_message),
                    "pr_id": pr_id,
                },
                idempotency_key=f"heimdall:{installation_id}:{pr_id}:{head_sha}:command:{uuid.uuid4()}",
            )


def _approver(
    sessions: async_sessionmaker[AsyncSession], reader: GitHubApiReader
) -> ApproveByMessage:
    """Bind the tenant services to Heimdall's approve-in-words hook."""

    async def approve(
        ports: MentionPorts, ctx: TenantCtx, ai: ChatModel, question: Question
    ) -> MentionAnswer | None:
        return await approve_from_message(sessions, reader, ports, ctx, ai, question)

    return approve


async def _run_for_card(
    sessions: async_sessionmaker[AsyncSession],
    reader: GitHubApiReader,
    model: ChatModel,
    ctx: TenantCtx,
    job: PrJobContext,
    installation_id: int,
) -> DeskResult | None:
    """Run Heimdall on a card job and return its result, or None when it did not finish."""
    snapshot = job.snapshot
    server = create_server(sessions, reader, AGENT_NAME)
    client = McpToolClient(server)
    skills = load_skills(AgentName.HEIMDALL, ALLOWED_TOOLS, REQUIRED_SKILLS)
    repro_summary = await _repro_summary(
        sessions, ctx, snapshot.id, snapshot.head_sha, client, installation_id
    )
    finding_summaries, finding_ids = await _finding_summaries_and_ids(
        client, installation_id, str(snapshot.id)
    )
    info = await reader.get_pull_request(
        RepoRef(ctx.installation_id, job.repo_full_name), snapshot.number
    )
    brief = build_desk_brief(
        installation_id=installation_id,
        pr_id=str(snapshot.id),
        repo_full_name=job.repo_full_name,
        number=snapshot.number,
        title=info.title,
        head_sha=snapshot.head_sha,
        repro_summary=repro_summary,
        finding_summaries=finding_summaries,
    )
    tracker = SavedIdTracker()
    guard = DeskGuard(existing_finding_ids=finding_ids)
    outcome = await run_heimdall(
        ai=model,
        toolbox=await _toolbox(server, skills, tracker, guard),
        submit=submit_desk_result_tool(tracker),
        head_sha=snapshot.head_sha,
        read_current_head=lambda: _current_head(sessions, ctx, snapshot.id),
        messages=build_messages(brief, _instructions(), skills.prompt_index()),
    )
    if not isinstance(outcome, Completed):
        log.info(
            "heimdall_did_not_submit",
            pr_id=str(snapshot.id),
            reason=type(outcome).__name__,
        )
        return None
    return outcome.payload


async def _run_for_command(
    sessions: async_sessionmaker[AsyncSession],
    reader: GitHubApiReader,
    model: ChatModel,
    ctx: TenantCtx,
    job: PrJobContext,
    installation_id: int,
    command_text: str,
) -> DeskResult | None:
    """Run Heimdall on a maintainer command and return its validated result."""
    snapshot = job.snapshot
    server = create_server(sessions, reader, AGENT_NAME)
    client = McpToolClient(server)
    skills = load_skills(AgentName.HEIMDALL, ALLOWED_TOOLS, REQUIRED_SKILLS)
    repro_summary = await _repro_summary(
        sessions, ctx, snapshot.id, snapshot.head_sha, client, installation_id
    )
    finding_summaries, finding_ids = await _finding_summaries_and_ids(
        client, installation_id, str(snapshot.id)
    )
    info = await reader.get_pull_request(
        RepoRef(ctx.installation_id, job.repo_full_name), snapshot.number
    )
    brief = build_desk_brief(
        installation_id=installation_id,
        pr_id=str(snapshot.id),
        repo_full_name=job.repo_full_name,
        number=snapshot.number,
        title=info.title,
        head_sha=snapshot.head_sha,
        repro_summary=repro_summary,
        finding_summaries=finding_summaries,
        command_text=command_text,
    )
    tracker = SavedIdTracker()
    guard = DeskGuard(existing_finding_ids=finding_ids)
    outcome = await run_heimdall(
        ai=model,
        toolbox=await _toolbox(server, skills, tracker, guard),
        submit=submit_desk_result_tool(tracker),
        head_sha=snapshot.head_sha,
        read_current_head=lambda: _current_head(sessions, ctx, snapshot.id),
        messages=build_messages(brief, _instructions(), skills.prompt_index()),
    )
    if not isinstance(outcome, Completed):
        log.info(
            "heimdall_command_did_not_submit",
            pr_id=str(snapshot.id),
            reason=type(outcome).__name__,
        )
        return None
    return outcome.payload


async def _toolbox(
    server: MCPServer,
    skills: SkillSet,
    tracker: SavedIdTracker,
    guard: DeskGuard,
) -> ToolBox:
    """Build Heimdall's ToolBox, with id tracking and guardrails on save tools."""
    tools = await mcp_agent_tools(server, ALLOWED_TOOLS)
    guarded: list[AgentTool] = []
    for tool in tools:
        if tool.spec.name == "save_draft":
            guarded.append(guard_save_draft(guard, _track_if_save(tool, tracker)))
        elif tool.spec.name == "save_card":
            guarded.append(guard_save_card(guard, _track_if_save(tool, tracker)))
        elif tool.spec.name == "create_mechanical_task":
            guarded.append(guard_create_mechanical_task(guard, _track_if_save(tool, tracker)))
        else:
            guarded.append(tool)
    return ToolBox(ALLOWED_TOOLS | {LOAD_SKILL_TOOL}, (*guarded, skills.tool()))


def _track_if_save(tool: AgentTool, tracker: SavedIdTracker) -> AgentTool:
    """Wrap a save tool so its returned id is recorded by the tracker."""
    save_tools = frozenset({"save_card", "save_draft", "create_mechanical_task"})
    if tool.spec.name not in save_tools:
        return tool

    original = tool.handler

    async def handler(arguments: Mapping[str, object]) -> object:
        result = await original(arguments)
        _record_id(tracker, tool.spec.name, result)
        return result

    return AgentTool(tool.spec, handler)


def _record_id(tracker: SavedIdTracker, tool_name: str, result: object) -> None:
    """Extract the artifact id from a save-tool result and hand it to the tracker."""
    if not isinstance(result, dict):
        return
    result_obj = cast(dict[str, object], result)
    id_key = {
        "save_card": "card_id",
        "save_draft": "draft_id",
        "create_mechanical_task": "task_id",
    }.get(tool_name)
    if id_key is None:
        return
    artifact_id = result_obj.get(id_key)
    if isinstance(artifact_id, str) and artifact_id:
        tracker.record(artifact_id)


async def _repro_summary(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    pr_id: uuid.UUID,
    head_sha: str,
    client: McpToolClient,
    installation_id: int,
) -> str:
    """Fetch the latest repro result and render it to a one-line summary."""
    run_id = await latest_run_id(sessions, ctx, pr_id, head_sha)
    if run_id is None:
        return ""
    result = await client.get_repro(installation_id, str(run_id))
    return _render_repro(result)


async def _finding_summaries_and_ids(
    client: McpToolClient, installation_id: int, pr_id: str
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Fetch accepted findings for the head and render each to a short summary."""
    result = await client.call("list_findings", installation_id=installation_id, pr_id=pr_id)
    raw_rows = result.get("result")
    if not isinstance(raw_rows, list):
        return (), ()
    rows = cast(list[object], raw_rows)
    summaries: list[str] = []
    ids: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = cast(dict[str, object], row)
        file = item.get("file")
        line = item.get("line")
        text = item.get("text")
        fid = item.get("id")
        if isinstance(fid, str) and fid:
            ids.append(fid)
        if isinstance(file, str) and isinstance(line, int) and isinstance(text, str):
            prefix = f"[{fid}] " if isinstance(fid, str) and fid else ""
            summaries.append(f"{prefix}{file}:{line} — {text}")
    return tuple(summaries), tuple(ids)


def _render_repro(result: dict[str, object]) -> str:
    """Render one repro result to a short status line."""
    try:
        status = ReproStatus(str(result["status"]))
        verdict_value = result.get("verdict")
        verdict = Verdict(str(verdict_value)) if verdict_value is not None else None
    except (KeyError, ValueError):
        return "Reproduction result is malformed."
    if status is ReproStatus.FINISHED and verdict is not None:
        return {
            Verdict.FIX_CONFIRMED: (
                "Fix confirmed: tests passed with the fix and failed without it."
            ),
            Verdict.FIX_FAILED: "Fix failed: tests did not pass with the proposed fix.",
            Verdict.NOT_REPRODUCED: (
                "Issue not reproduced: tests passed with and without the fix."
            ),
        }[verdict]
    return {
        ReproStatus.TIMED_OUT: "Reproduction timed out; no verdict is available.",
        ReproStatus.STALE: "Reproduction is stale because the PR head changed.",
    }.get(status, "Reproduction is still running; no verdict is available.")


async def _saved_message_for(
    session: AsyncSession,
    ctx: TenantCtx,
    result: DeskResult,
    job: PrJobContext,
) -> str | None:
    """Build the trusted approval message or task block for saved work, or None for explanation."""
    if result.action == "explanation":
        return None
    if result.action == "card":
        draft = await get_current_draft_in_transaction(session, ctx, job.snapshot.id, "switch_card")
        return format_approval_message(
            draft.body,
            draft.id,
            draft.body_hash,
            int(ctx.installation_id),
            job.repo_full_name,
        )
    if result.action == "draft":
        if result.draft_id is None:
            return None
        draft = await get_draft_in_transaction(session, ctx, uuid.UUID(result.draft_id))
        return format_approval_message(
            draft.body,
            draft.id,
            draft.body_hash,
            int(ctx.installation_id),
            job.repo_full_name,
        )
    if result.action == "mechanical_task":
        if result.task_id is None:
            return None
        return f"Mechanical task created: {result.task_id}"
    return None


async def _current_head(
    sessions: async_sessionmaker[AsyncSession], ctx: TenantCtx, pr_id: uuid.UUID
) -> str:
    """Read the PR's current head, so a result for a stale head is dropped."""
    async with unit_of_work(sessions) as session:
        snapshot = await get_pr_snapshot(session, ctx, pr_id)
    if snapshot is None:
        raise ValueError(f"pull request {pr_id} is not registered for this installation")
    return snapshot.head_sha


def _instructions() -> str:
    """Return Heimdall's role prompt, read from ``prompt.md`` beside this module."""
    return PROMPT_PATH.read_text(encoding="utf-8")


async def run_worker() -> None:
    """Consume Heimdall's card jobs from the shared Postgres queue."""
    from heimdall.shared.logging import configure_logging
    from heimdall.shared.settings import get_settings

    settings = get_settings()
    configure_logging(settings.log_level)
    # Validated at startup so a broken playbook fails before any job runs.
    load_skills(AgentName.HEIMDALL, ALLOWED_TOOLS, REQUIRED_SKILLS)
    # Built at startup so a bad LLM_HEIMDALL_* / LLM_DEFAULT_* config fails before any job runs.
    ai = AiAdapter(load_ai_config())
    database_url = settings.database_url.get_secret_value()
    engine = create_engine(database_url)
    clients = GitHubAppClients(
        settings.github_app_id, load_private_key(settings.github_private_key_path)
    )
    queue_app = create_queue_app(database_url)
    sessions = make_session_factory(engine)
    register_heimdall_task(queue_app, sessions, GitHubApiReader(clients), ai)
    try:
        async with queue_app.open_async():
            await run_with_presence(
                AgentName.HEIMDALL,
                sessions,
                lambda: queue_app.run_worker_async(queues=[HEIMDALL_QUEUE]),
                default_room_id=settings.default_switch_agent_room_id,
            )
    finally:
        await engine.dispose()
        await ai.aclose()


def main() -> None:
    """Start the dedicated Heimdall queue consumer."""
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
