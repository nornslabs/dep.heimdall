"""Check a reply against what this run's tools actually returned and did.

A model can cite a finding id or a file it never read. ``EvidenceTracker`` wraps every tool
in a mention run and records each result and each write that ran; ``reply_problems`` then
refuses any evidence no tool returned and any action that did not happen, as the run's one
repair round.
"""

import json
import re
from collections.abc import Mapping
from typing import Final, Literal

from agents._reply import ActionTaken, AgentReply, evidence_token
from agents._toolbox import AgentTool
from heimdall.shared.agents import AgentName

MentionMode = Literal["ask", "act"]

REFUSAL_PREFIX: Final = "error:"
"""How guards refuse a call: they return this text instead of running the tool."""

ACTION_FOR_TOOL: Final[Mapping[str, ActionTaken]] = {
    "start_repro": "repro_started",
    "save_findings": "findings_saved",
    "save_draft": "draft_saved",
    "draft_close": "draft_saved",
    "draft_reopen": "draft_saved",
    "draft_merge": "draft_saved",
    "draft_ask_contributor": "draft_saved",
}
"""The write tools act mode may open, and what the reply must report after each one runs."""


class EvidenceTracker:
    """Records what one mention run's tools returned and which write tools ran."""

    def __init__(self) -> None:
        """Start with nothing seen and nothing run."""
        self._results: list[str] = []
        self._actions: set[ActionTaken] = set()

    def wrap(self, tool: AgentTool) -> AgentTool:
        """Return the tool with its successful results recorded.

        Args:
            tool: A tool offered in this run.

        Returns:
            The same tool; a call that raises or that a guard refused records nothing.
        """
        original = tool.handler
        name = tool.spec.name

        async def handler(arguments: Mapping[str, object]) -> object:
            result = await original(arguments)
            if isinstance(result, str) and result.startswith(REFUSAL_PREFIX):
                # A guard refused the call: nothing ran and nothing was returned to cite.
                return result
            self._results.append(result if isinstance(result, str) else json.dumps(result))
            action = ACTION_FOR_TOOL.get(name)
            if action is not None:
                self._actions.add(action)
            return result

        return AgentTool(tool.spec, handler)

    def seen(self, token: str) -> bool:
        """Say whether any tool result in this run contained ``token`` as a whole token.

        A bare substring is not enough: ``R-3`` must not count as seen because ``R-31`` was.
        """
        pattern = re.compile(rf"(?<![\w-]){re.escape(token)}(?![\w-])")
        return any(pattern.search(result) for result in self._results)

    def actions(self) -> frozenset[ActionTaken]:
        """Return the writes that ran in this run."""
        return frozenset(self._actions)


def reply_problems(
    reply: AgentReply, tracker: EvidenceTracker, *, agent: AgentName, mode: MentionMode
) -> tuple[str, ...]:
    """List what a schema-valid reply claims that this run cannot back.

    Args:
        reply: The submission that passed schema validation.
        tracker: What this run's tools returned and did.
        agent: The replying agent, so it cannot redirect to itself.
        mode: ``ask`` allows no action; ``act`` requires the action that actually ran.

    Returns:
        One line per problem, naming the field; empty when the reply stands.
    """
    problems: list[str] = []
    if reply.redirect_to == agent.value:
        problems.append("redirect_to: you cannot redirect to yourself")
    for entry in reply.evidence:
        if not tracker.seen(evidence_token(entry)):
            problems.append(f"evidence: no tool in this run returned {entry}")
    problems.extend(_action_problems(reply.action_taken, tracker.actions(), mode))
    return tuple(problems)


def _action_problems(
    claimed: ActionTaken, ran: frozenset[ActionTaken], mode: MentionMode
) -> tuple[str, ...]:
    """Compare the action a reply reports with the writes that ran."""
    if mode == "ask":
        if claimed != "none":
            return ("action_taken: ask mode cannot act; use 'none'",)
        return ()
    if claimed != "none" and claimed not in ran:
        return (f"action_taken: {claimed!r} but no matching tool ran",)
    if claimed == "none" and ran:
        return (f"action_taken: report {sorted(ran)[0]!r}; that tool ran",)
    return ()
