"""Drive the real Heimdall pipeline in-process against the fake GitHub and Switch.

Everything below the fakes is production code: webhook intake, procrastinate jobs on both
queues, the outbox senders, the MCP tool server, the agents' own loops and the bridge's
approval handler. The pipeline also stands in for the room-event dispatch that would wake
Loki and Heimdall, since no production code does that yet.
"""

import uuid
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from procrastinate import App
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from agents.heimdall.agent import CardResult, build_review_card
from agents.huginn.runner import register_huginn_task
from agents.loki.agent import LokiResult, run_loki
from apps.bridge.approval import ApprovalReactionHandler
from apps.mcp.main import create_server
from apps.worker.tasks import (
    register_actions_result_task,
    register_intake_task,
    register_repro_timeout_task,
    register_triage_task,
)
from heimdall.adapters.db.outbox.sender import ChannelSender, send_due_messages
from heimdall.adapters.github.actions import GitHubActionsResults
from heimdall.adapters.github.auth import GitHubAppClients
from heimdall.adapters.github.reader import GitHubApiReader
from heimdall.adapters.github.sender import GitHubSender
from heimdall.adapters.queue.app import create_queue_app
from heimdall.adapters.queue.enqueue import DEFAULT_QUEUE, HUGINN_QUEUE
from heimdall.adapters.switch.ports import RoomEvent
from heimdall.modules.tenancy.api import on_installation_event
from heimdall.modules.triage.api import receive_webhook
from tests.e2e.fake_world import (
    DEMO_MAIN_SHA,
    INSTALLATION_ID,
    REPO_FULL_NAME,
    REPO_ID,
    ROOM_ID,
    DemoGitHub,
    FakeSwitch,
    SeededPr,
    pull_request_webhook,
    workflow_run_webhook,
)
from tests.e2e.mcp_tools import HeimdallMcpTools, LokiMcpTools, ScriptedModel
from tests.integration.db.installation_helpers import created_installation

MAX_SETTLE_ROUNDS = 20
RULES_SUMMARY = "All intake rules passed."
SUGGESTED_ACTION = "Approve and merge."

_PR_COLUMN = (
    "SELECT {column} FROM prs WHERE installation_id = :installation_id "
    "AND repo_id = :repo_id AND number = :number"
)
_LATEST_RUN = (
    "SELECT r.id FROM repro_runs r JOIN prs p ON p.id = r.pr_id "
    "WHERE p.installation_id = :installation_id AND p.repo_id = :repo_id "
    "AND p.number = :number AND r.head_sha = p.head_sha ORDER BY r.attempt DESC LIMIT 1"
)
_LINK_ROOM = "UPDATE repos SET switch_room_id = :room WHERE repo_id = :repo"
# The schema is shared by the whole session, so failures are read only past this test's start.
_LAST_JOB_ID = "SELECT coalesce(max(id), 0) FROM procrastinate_jobs"
_FAILED_JOBS = (
    "SELECT task_name FROM procrastinate_jobs "
    "WHERE status = 'failed' AND id > :since_job ORDER BY id"
)
_OUTBOX_ERRORS = (
    "SELECT kind, last_error FROM outbox "
    "WHERE last_error IS NOT NULL AND created_at >= :since ORDER BY created_at"
)


@dataclass(frozen=True)
class PipelineDeps:
    """Database and fakes one replay runs against."""

    sessions: async_sessionmaker[AsyncSession]
    database_url: str
    db_schema: str
    github: DemoGitHub
    switch: FakeSwitch
    clients: GitHubAppClients


class Pipeline:
    """One installation of Heimdall on the demo repo, advanced step by step by the tests."""

    def __init__(self, deps: PipelineDeps) -> None:
        """Wire production adapters and job handlers to the fakes."""
        self._deps = deps
        self.sessions = deps.sessions
        self.reader = GitHubApiReader(deps.clients)
        self._senders: dict[str, ChannelSender] = {
            "github": GitHubSender(deps.sessions, deps.clients),
            "switch": deps.switch,
        }
        self._main_queue = self._build_main_queue()
        self._huginn_queue = create_queue_app(deps.database_url, schema=deps.db_schema)
        register_huginn_task(self._huginn_queue, deps.sessions, sleep=self._advance)
        self._deliveries = 0
        self._started_at = datetime.now(UTC)
        self._since_job = 0

    @asynccontextmanager
    async def running(self) -> AsyncGenerator["Pipeline"]:
        """Open both queue apps and install Heimdall on the demo repo."""
        async with self._main_queue.open_async(), self._huginn_queue.open_async():
            self._started_at = datetime.now(UTC)
            last_job = await self._query(_LAST_JOB_ID, {})
            self._since_job = int(str(last_job[0][0]))
            await self._install()
            yield self

    async def open_pr(self, pr: SeededPr) -> None:
        """Deliver ``pull_request.opened`` and run everything it sets off on the main worker."""
        self._deps.github.seed(pr)
        await self._deliver("pull_request", "opened", pull_request_webhook(pr, "opened"))
        await self.settle()

    async def run_huginn(self) -> None:
        """Run queued Huginn jobs the way the ``huginn`` service would."""
        await self._huginn_queue.run_worker_async(queues=[HUGINN_QUEUE], wait=False)
        await self.settle()

    async def wake_loki(self, pr: SeededPr, model_reply: str) -> LokiResult:
        """Deliver ``pr.ready_for_review`` to Loki with a scripted model reply."""
        tools = LokiMcpTools(create_server(self.sessions, self.reader, "loki"))
        model = ScriptedModel(model_reply)
        pr_id = await self.pr_id(pr)
        result = await run_loki(tools, model, INSTALLATION_ID, pr_id, REPO_FULL_NAME)
        await self.settle()
        return result

    async def wake_heimdall(self, pr: SeededPr) -> tuple[CardResult, str]:
        """Deliver ``pr.ready_for_card`` to Heimdall and post its approval message to the room.

        Returns:
            The saved card and draft, and the room post id carrying the approval marker.
        """
        tools = HeimdallMcpTools(create_server(self.sessions, self.reader, "heimdall"))
        card = await build_review_card(
            tools,
            INSTALLATION_ID,
            await self.pr_id(pr),
            pr.number,
            REPO_FULL_NAME,
            pr.title,
            pr.head_sha,
            RULES_SUMMARY,
            SUGGESTED_ACTION,
            await self.latest_repro_run(pr),
        )
        return card, self._deps.switch.post(card.approval_message)

    async def react_thumbs_up(self, post_id: str, login: str) -> None:
        """Have a Switch user react 👍 on a room post, then deliver what the bridge queued."""
        user_id = f"user-{login}"
        self._deps.switch.users[user_id] = login
        handler = ApprovalReactionHandler(self.sessions, self.reader, self._deps.switch)
        event = RoomEvent("reaction_added", ROOM_ID, post_id, user_id, emoji_name="+1")
        await handler.handle(event)
        await self.settle()

    async def settle(self) -> None:
        """Run default-queue jobs, send due outbox rows and report finished workflow runs.

        Repeats until a round changes nothing, so a webhook's whole chain of effects lands.

        Raises:
            AssertionError: the system was still moving after ``MAX_SETTLE_ROUNDS``.
        """
        for _round in range(MAX_SETTLE_ROUNDS):
            await self._main_queue.run_worker_async(queues=[DEFAULT_QUEUE], wait=False)
            sent = await send_due_messages(self.sessions, self._senders, now=datetime.now(UTC))
            finished = self._deps.github.take_finished_runs()
            for run_id in finished:
                payload = workflow_run_webhook(run_id, DEMO_MAIN_SHA)
                await self._deliver("workflow_run", "completed", payload)
            if sent == 0 and not finished:
                return
        raise AssertionError(f"pipeline still moving after {MAX_SETTLE_ROUNDS} rounds")

    async def pr_id(self, pr: SeededPr) -> uuid.UUID:
        """Return the triage row id for a seeded PR."""
        return uuid.UUID(str(await self._scalar(_PR_COLUMN.format(column="id"), pr)))

    async def pr_state(self, pr: SeededPr) -> str:
        """Return the stored triage state for a seeded PR."""
        return str(await self._scalar(_PR_COLUMN.format(column="state"), pr))

    async def latest_repro_run(self, pr: SeededPr) -> uuid.UUID | None:
        """Return the newest repro attempt for a PR's head, if any."""
        value = await self._scalar(_LATEST_RUN, pr)
        return uuid.UUID(str(value)) if value is not None else None

    async def failed_jobs(self) -> list[str]:
        """Return the names of queue jobs this replay ran that ended in failure."""
        rows = await self._query(_FAILED_JOBS, {"since_job": self._since_job})
        return [str(row[0]) for row in rows]

    async def outbox_errors(self) -> list[str]:
        """Return ``kind: error`` for every outbox row this replay failed to send."""
        rows = await self._query(_OUTBOX_ERRORS, {"since": self._started_at})
        return [f"{row[0]}: {row[1]}" for row in rows]

    def _build_main_queue(self) -> App:
        """Register the main worker's jobs exactly as ``apps/worker/main.py`` does."""
        deps = self._deps
        queue_app = create_queue_app(deps.database_url, schema=deps.db_schema)
        register_intake_task(queue_app, deps.sessions, self.reader)
        register_triage_task(queue_app, deps.sessions)
        register_repro_timeout_task(queue_app, deps.sessions)
        register_actions_result_task(queue_app, deps.sessions, GitHubActionsResults(deps.clients))
        return queue_app

    async def _install(self) -> None:
        """Install the app on the demo repo and link the repo to the triage room."""
        installation = created_installation(INSTALLATION_ID, "nornslabs", REPO_ID, REPO_FULL_NAME)
        await on_installation_event(self.sessions, installation)
        # No production code sets the room link yet; the replay sets it like an operator would.
        async with self.sessions() as session, session.begin():
            connection = await session.connection()
            await connection.execute(text(_LINK_ROOM), {"room": ROOM_ID, "repo": REPO_ID})

    async def _advance(self, _seconds: float) -> None:
        """Huginn's poll wait: let the rest of the system move instead of sleeping."""
        await self.settle()

    async def _deliver(self, event: str, action: str, payload: dict[str, object]) -> None:
        """Store a webhook as the API would after verifying its signature."""
        self._deliveries += 1
        delivery_id = f"e2e-{self._deliveries}-{uuid.uuid4().hex[:8]}"
        await receive_webhook(self.sessions, delivery_id, event, action, payload)  # pyright: ignore[reportArgumentType]

    async def _scalar(self, sql: str, pr: SeededPr) -> object:
        """Run a one-value query scoped to the demo installation and a PR number."""
        params = {"installation_id": INSTALLATION_ID, "repo_id": REPO_ID, "number": pr.number}
        rows = await self._query(sql, params)
        return rows[0][0] if rows else None

    async def _query(self, sql: str, params: Mapping[str, object]) -> list[tuple[object, ...]]:
        """Run a read-only SQL query and return every row as a plain tuple."""
        async with self.sessions() as session:
            connection = await session.connection()
            result = await connection.execute(text(sql), dict(params))
            return [tuple(row) for row in result.all()]
