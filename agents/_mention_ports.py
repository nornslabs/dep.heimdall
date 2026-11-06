"""The database and tool-server side of answering a mention: ``MentionPorts`` for one job.

Each job gets its own ports, bound to one tenant. Tools come from the agent's own MCP server;
in act mode the agent's guard is wrapped around its write tool before the model sees it.
"""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from mcp.server.mcpserver import MCPServer
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from agents._conversation import (
    MentionAgent,
    MentionAnswer,
    MentionMode,
    MentionPorts,
    MentionPr,
    Question,
)
from agents._mcp_tools import mcp_agent_tools
from agents._mention import SENDER_ROLE_MAINTAINER
from agents._runtime import AgentTool, ChatModel
from apps.mcp.main import create_server
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.adapters.github.reader import GitHubApiReader
from heimdall.modules.tenancy.api import get_repo_full_name_in_transaction, repo_maintainers
from heimdall.modules.triage.api import PrSnapshot, get_pr_snapshot
from heimdall.shared.context import TenantCtx


@dataclass(frozen=True)
class ActSetup:
    """What an agent's act-mode guard may read to build its guard.

    Attributes:
        server: The agent's MCP server for this job.
        sessions: Tenant database session factory.
        reader: Read-only GitHub adapter.
        ctx: Tenant and request context.
        pr: The PR being acted on.
        sender: Login of the maintainer whose message asked for the action; empty if unknown.
    """

    server: MCPServer
    sessions: async_sessionmaker[AsyncSession]
    reader: GitHubApiReader
    ctx: TenantCtx
    pr: MentionPr
    sender: str = ""


type GuardActTools = Callable[[ActSetup, tuple[AgentTool, ...]], Awaitable[tuple[AgentTool, ...]]]
"""Wraps an agent's guard around its write tool; returns the tools with the guard in place."""


type ApproveByMessage = Callable[
    [MentionPorts, TenantCtx, ChatModel, Question], Awaitable[MentionAnswer | None]
]
"""An agent's first look at a mention: an answer when the message settles a pending approval,
or None to let the normal ask/act conversation handle it."""


from heimdall.adapters.switch.agent.client import SwitchAgentClient

# ... existing imports ...

@dataclass(frozen=True)
class MentionDeps:
    """Everything one agent's ``agent.mention`` task needs, built once by its runner.

    Attributes:
        agent: The agent's scope, prompt, tool sets and budget.
        sessions: Tenant database session factory.
        reader: Read-only GitHub adapter.
        model: Returns the agent's model; lazy so a worker without one still registers.
        guard_act_tools: Puts the agent's guard on its write tool in act mode.
        approve_by_message: Optional first look at each mention, for an agent whose drafts a
            maintainer approves in words (Heimdall).
        poster: Switch agent client for feedback features (typing, reactions). Optional; if
            None, feedback features are disabled.
    """

    agent: MentionAgent
    sessions: async_sessionmaker[AsyncSession]
    reader: GitHubApiReader
    model: Callable[[], ChatModel]
    guard_act_tools: GuardActTools
    approve_by_message: ApproveByMessage | None = None
    poster: SwitchAgentClient | None = None


class DbMentionPorts:
    """``MentionPorts`` for one job, reading one tenant's PRs and maintainer lists."""

    def __init__(
        self, deps: MentionDeps, ctx: TenantCtx, sender_role: str, sender: str = ""
    ) -> None:
        """Bind the ports to one tenant and the sender's role.

        Args:
            deps: The agent's mention dependencies.
            ctx: Tenant and request context for this job.
            sender_role: The role the bridge gave the sender; only a maintainer may act.
            sender: The sender's login, so an approval is recorded under their name.
        """
        self._deps = deps
        self._ctx = ctx
        self._sender_role = sender_role
        self._sender = sender

    # Feedback features (Phase 1)

    async def send_typing(self, channel_id: str) -> None:
        """Send a typing indicator in ``channel_id``. No-op if not configured."""
        if self._deps.poster:
            await self._deps.poster.send_typing(channel_id)

    async def add_reaction(self, post_id: str, emoji_name: str) -> None:
        """Add a reaction to ``post_id``. No-op if not configured."""
        if self._deps.poster:
            await self._deps.poster.add_reaction(post_id, emoji_name)

    async def remove_reaction(self, post_id: str, emoji_name: str) -> None:
        """Remove a reaction from ``post_id``. No-op if not configured."""
        if self._deps.poster:
            await self._deps.poster.remove_reaction(post_id, emoji_name)

    async def post_progress(
        self,
        channel_id: str,
        thread_id: str | None,
        text: str,
        *,
        update_post_id: str | None = None,
    ) -> str | None:
        """Post or update one progress message. No-op when the poster is not configured."""
        if self._deps.poster is None:
            return None
        return await self._deps.poster.post_progress(
            channel_id, thread_id, text, update_post_id=update_post_id
        )

    async def load_pr(self, pr_ref: str) -> MentionPr | None:
        """Return the PR for ``pr_ref``, or None when it is malformed or not this tenant's."""
        try:
            pr_id = uuid.UUID(pr_ref)
        except ValueError:
            return None
        async with unit_of_work(self._deps.sessions) as session:
            snapshot = await get_pr_snapshot(session, self._ctx, pr_id)
            if snapshot is None:
                return None
            repo = await get_repo_full_name_in_transaction(session, self._ctx, snapshot.repo_id)
        return MentionPr(
            installation_id=int(self._ctx.installation_id),
            pr_id=str(snapshot.id),
            repo_id=int(snapshot.repo_id),
            repo_full_name=repo,
            number=snapshot.number,
            head_sha=snapshot.head_sha,
        )

    async def is_maintainer(self, pr: MentionPr, sender: str) -> bool:
        """Check the sender's role and the repository's maintainer list, ignoring case."""
        if self._sender_role != SENDER_ROLE_MAINTAINER:
            return False
        async with unit_of_work(self._deps.sessions) as session:
            snapshot = await self._snapshot(session, pr)
            maintainers = await repo_maintainers(session, self._ctx, snapshot.repo_id)
        return sender.casefold() in {login.casefold() for login in maintainers}

    async def read_head(self, pr: MentionPr) -> str:
        """Read the PR's head now."""
        async with unit_of_work(self._deps.sessions) as session:
            snapshot = await self._snapshot(session, pr)
        return snapshot.head_sha

    async def tools(self, pr: MentionPr, mode: MentionMode) -> Sequence[AgentTool]:
        """Return the mode's tools from the agent's server, with guards in act mode."""
        agent = self._deps.agent
        server = create_server(self._deps.sessions, self._deps.reader, agent.scope.agent.value)
        names = agent.act_tools if mode == "act" else agent.ask_tools
        tools = await mcp_agent_tools(server, names)
        if mode == "ask":
            return tools
        setup = ActSetup(
            server, self._deps.sessions, self._deps.reader, self._ctx, pr, self._sender
        )
        return await self._deps.guard_act_tools(setup, tools)

    async def _snapshot(self, session: AsyncSession, pr: MentionPr) -> PrSnapshot:
        """Read the PR again inside ``session``.

        Raises:
            ValueError: the PR is no longer registered for this tenant.
        """
        snapshot = await get_pr_snapshot(session, self._ctx, uuid.UUID(pr.pr_id))
        if snapshot is None:
            raise ValueError(f"pull request {pr.pr_id} is not registered for this installation")
        return snapshot
