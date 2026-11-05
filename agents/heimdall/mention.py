"""Heimdall's conversation setup, and the guard act mode puts on ``save_draft``."""

from dataclasses import replace
from pathlib import Path
from typing import cast

from agents._conversation import MENTION_MAX_TURNS, MentionAgent
from agents._mcp_tools import McpToolClient
from agents._mention_ports import ActSetup
from agents._runtime import AgentTool
from agents.heimdall.allowed_tools import ACT_TOOLS, ASK_TOOLS, DRAFT_TOOLS
from agents.heimdall.config import BUDGET
from agents.heimdall.guardrails import DeskGuard, guard_save_draft
from agents.heimdall.scope import SCOPE

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
    """Cap drafts and keep their finding citations real, as the command job does.

    Args:
        setup: The job's server, database, reader, tenant and PR.
        tools: The act-mode tools from Heimdall's server.

    Returns:
        The same tools with ``save_draft`` guarded.
    """
    finding_ids = await _finding_ids(McpToolClient(setup.server), setup)
    guard = DeskGuard(existing_finding_ids=finding_ids)
    return tuple(
        guard_save_draft(guard, tool) if tool.spec.name in DRAFT_TOOLS else tool for tool in tools
    )


async def _finding_ids(client: McpToolClient, setup: ActSetup) -> tuple[str, ...]:
    """Return the ids of the PR's accepted findings, the only ones a draft may cite."""
    result = await client.call(
        "list_findings", installation_id=setup.pr.installation_id, pr_id=setup.pr.pr_id
    )
    rows = result.get("result")
    if not isinstance(rows, list):
        return ()
    ids: list[str] = []
    for row in cast(list[object], rows):
        if not isinstance(row, dict):
            continue
        finding_id = cast(dict[str, object], row).get("id")
        if isinstance(finding_id, str) and finding_id:
            ids.append(finding_id)
    return tuple(ids)
