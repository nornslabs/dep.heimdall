"""Loki's conversation setup, and the guard act mode puts on ``save_findings``."""

from dataclasses import replace
from pathlib import Path

from agents._conversation import MENTION_MAX_TURNS, MentionAgent
from agents._mention_ports import ActSetup
from agents._runtime import AgentTool
from agents.loki._diff import diff_added_lines
from agents.loki.allowed_tools import ACT_TOOLS, ASK_TOOLS
from agents.loki.config import BUDGET
from agents.loki.guardrails import FindingGuard, guard_save_findings, is_docs_only
from agents.loki.scope import SCOPE
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
    """Keep saved findings inside the diff and off docs-only PRs, as the review job does.

    Args:
        setup: The job's server, database, reader, tenant and PR.
        tools: The act-mode tools from Loki's server.

    Returns:
        The same tools with ``save_findings`` guarded.
    """
    repo = RepoRef(setup.ctx.installation_id, setup.pr.repo_full_name)
    config = await load_config(setup.sessions, setup.reader, setup.ctx, repo, setup.pr.head_sha)
    changed = await setup.reader.list_changed_files(repo, setup.pr.number)
    diff = await setup.reader.get_diff(repo, setup.pr.number)
    guard = FindingGuard(
        diff_lines=diff_added_lines(diff.text),
        docs_only=is_docs_only([file.path for file in changed], config.review.skip_paths),
    )
    return tuple(
        guard_save_findings(guard, tool) if tool.spec.name == "save_findings" else tool
        for tool in tools
    )
