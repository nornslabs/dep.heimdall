"""The shared agent loop: guarded tools, budgets, a validated submit and a typed outcome.

The model decides what to do next; this loop decides what it may do. Every run ends in a
``RunOutcome`` and nothing here posts to Switch or GitHub. Agents import the runtime from
this file only; the other ``agents/_*.py`` helpers are its private parts.
"""

from collections.abc import Sequence

from pydantic import BaseModel

from agents._budget import AgentBudget
from agents._guardrails import DATA_RULE
from agents._outcome import (
    Completed,
    CouldNotFinish,
    CouldNotFinishReason,
    RunOutcome,
    RunUsage,
    Stale,
    Unavailable,
)
from agents._submit import SubmitTool
from agents._toolbox import AgentTool, ToolBox
from agents._turn import AgentRun, ChatModel, RunState, still_current, take_turn
from heimdall.adapters.ai import ChatMessage
from heimdall.shared.logging import get_logger

__all__ = [
    "DATA_RULE",
    "AgentBudget",
    "AgentRun",
    "AgentTool",
    "ChatModel",
    "Completed",
    "CouldNotFinish",
    "RunOutcome",
    "RunUsage",
    "Stale",
    "SubmitTool",
    "ToolBox",
    "Unavailable",
    "run_agent",
    "still_current",
]

log = get_logger(__name__)


async def run_agent[T: BaseModel](
    run: AgentRun[T], messages: Sequence[ChatMessage]
) -> RunOutcome[T]:
    """Let the model call its tools until it submits a valid answer or hits a limit.

    Args:
        run: The agent's model, tools, submit schema, budget and head check.
        messages: Prompt to start from; never modified.

    Returns:
        ``Completed`` with the validated answer; ``CouldNotFinish`` when the turns, the
        token budget or the one repair round ran out; ``Unavailable`` when the model or a
        tool stayed down; ``Stale`` when the head moved before the answer was submitted.
    """
    state = RunState(_with_data_rule(messages), turn_limit=run.budget.max_turns)
    outcome: RunOutcome[T] | None = None
    while outcome is None:
        limit = _limit_reached(run.budget, state)
        if limit is not None:
            outcome = CouldNotFinish(limit, state.usage, state.turns)
        else:
            state, outcome = await take_turn(run, state)
    _log_finished(run.agent, outcome)
    return outcome


def _with_data_rule(messages: Sequence[ChatMessage]) -> tuple[ChatMessage, ...]:
    """Return the prompt with the ``pr_data`` rule leading the system turn, adding one if absent.

    The runtime wraps every tool result, so the runtime states what the wrapper means; no
    agent prompt can leave it out.
    """
    if messages and messages[0].role == "system":
        system = ChatMessage("system", f"{DATA_RULE}\n\n{messages[0].content}")
        return (system, *messages[1:])
    return (ChatMessage("system", DATA_RULE), *messages)


def _limit_reached(budget: AgentBudget, state: RunState) -> CouldNotFinishReason | None:
    """Name the limit that stops the run before its next model call, if any."""
    if state.turns >= state.turn_limit:
        return "turn_limit"
    if state.usage.tokens >= budget.run_tokens:
        return "token_budget"
    return None


def _log_finished[T](agent: str, outcome: RunOutcome[T]) -> None:
    """Log how the run ended, with counts only, never PR or model text."""
    log.info(
        "agent_run_finished",
        agent=agent,
        outcome=type(outcome).__name__,
        turns=outcome.turns,
        input_tokens=outcome.usage.usage.input_tokens,
        output_tokens=outcome.usage.usage.output_tokens,
    )
