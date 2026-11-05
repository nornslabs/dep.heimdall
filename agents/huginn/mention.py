"""Huginn's conversation setup, and the guard act mode puts on ``start_repro``."""

from dataclasses import replace
from pathlib import Path

from agents._conversation import MENTION_MAX_TURNS, MentionAgent
from agents._mention_ports import ActSetup
from agents._runtime import AgentTool
from agents.huginn.allowed_tools import ACT_TOOLS, ASK_TOOLS
from agents.huginn.config import BUDGET
from agents.huginn.guardrails import ReproGuard, guard_start_repro
from agents.huginn.scope import SCOPE
from heimdall.modules.tenancy.api import load_config
from heimdall.shared.github import RepoRef

CONVERSATION_PATH = Path(__file__).with_name("conversation.md")
MENTION_BUDGET = replace(BUDGET, max_turns=MENTION_MAX_TURNS)

MENTION_AGENT = MentionAgent(
    scope=SCOPE,
    instructions=CONVERSATION_PATH.read_text(encoding="utf-8"),
    ask_tools=ASK_TOOLS,
    act_tools=ACT_TOOLS,
    budget=MENTION_BUDGET,
)


async def guard_act_tools(setup: ActSetup, tools: tuple[AgentTool, ...]) -> tuple[AgentTool, ...]:
    """Wrap the repo's runner allow-list around ``start_repro``, as the repro job does.

    Args:
        setup: The job's server, database, reader, tenant and PR.
        tools: The act-mode tools from Huginn's server.

    Returns:
        The same tools with ``start_repro`` guarded.
    """
    repo = RepoRef(setup.ctx.installation_id, setup.pr.repo_full_name)
    config = await load_config(setup.sessions, setup.reader, setup.ctx, repo, setup.pr.head_sha)
    guard = ReproGuard(config.repro.allowed_runners)
    return tuple(
        guard_start_repro(guard, tool) if tool.spec.name == "start_repro" else tool
        for tool in tools
    )
