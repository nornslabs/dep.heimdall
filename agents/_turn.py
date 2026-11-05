"""One turn of an agent run: call the model, then run or refuse each tool it asked for.

Every step returns a new ``RunState``; an outcome alongside it means the run is over.
"""

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol

from pydantic import BaseModel

from agents._budget import AgentBudget, CallCounts, estimate_tokens
from agents._guardrails import ToneFiltered, filter_tone, find_secret_kinds
from agents._outcome import Completed, CouldNotFinish, RunOutcome, RunUsage, Stale, Unavailable
from agents._submit import InvalidSubmission, SubmitTool
from agents._tool_result import result_for_model
from agents._toolbox import ToolBox
from heimdall.adapters.ai import ChatMessage, ChatResponse, ToolCall, ToolChoice, ToolSpec
from heimdall.shared.errors import (
    AiBadResponse,
    AiRequestFailed,
    AiUnavailable,
    HeimdallError,
    ToolUnavailable,
)
from heimdall.shared.logging import get_logger

MODEL_ERRORS = (AiUnavailable, AiBadResponse, AiRequestFailed)

type Step[T] = tuple["RunState", RunOutcome[T] | None]

log = get_logger(__name__)


class ChatModel(Protocol):
    """What agents need from a model: ``AiAdapter`` in production, a script in tests."""

    @property
    def model(self) -> str:
        """The configured model name."""
        ...

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        *,
        tool_choice: ToolChoice = "auto",
    ) -> ChatResponse:
        """Send one chat turn and return the reply."""
        ...


@dataclass(frozen=True)
class AgentRun[T: BaseModel]:
    """Everything one agent run needs: model, tools, finish schema, limits and head check."""

    agent: str
    ai: ChatModel
    toolbox: ToolBox
    submit: SubmitTool[T]
    budget: AgentBudget
    head_sha: str
    read_current_head: Callable[[], Awaitable[str]]


@dataclass(frozen=True)
class RunState:
    """The conversation and counters so far.

    Attributes:
        history: Every message sent and received, in order.
        turn_limit: Model calls allowed; raised by one to make room for a repair.
        usage: Tokens spent so far.
        counts: Tool calls that actually ran, per tool.
        turns: Model calls made.
        repaired: True once the one repair round has been used.
    """

    history: tuple[ChatMessage, ...]
    turn_limit: int
    usage: RunUsage = field(default_factory=RunUsage)
    counts: CallCounts = field(default_factory=CallCounts)
    turns: int = 0
    repaired: bool = False


async def still_current(
    head_sha: str, read_current_head: Callable[[], Awaitable[str]], *, agent: str
) -> bool:
    """Check the PR head right before saving, so work on an old commit is dropped.

    Args:
        head_sha: Head the agent's work was based on.
        read_current_head: Reads the PR's head now.
        agent: Agent name for the log line.

    Returns:
        True when the head is unchanged; False (and a log line) when it moved.
    """
    current = await read_current_head()
    if current == head_sha:
        return True
    log.info("agent_result_dropped_head_changed", agent=agent)
    return False


async def take_turn[T: BaseModel](run: AgentRun[T], state: RunState) -> Step[T]:
    """Make one model call and act on its reply.

    Args:
        run: The agent's model, tools, submit schema, budget and head check.
        state: The run so far.

    Returns:
        The new state, and an outcome when this turn ended the run.
    """
    tools = (*run.toolbox.specs(), run.submit.spec)
    try:
        reply = await run.ai.chat(state.history, tools, tool_choice="required")
    except MODEL_ERRORS as error:
        return state, Unavailable(type(error).__name__, state.usage, state.turns)
    if reply.usage.input_tokens == 0 and reply.usage.output_tokens == 0:
        log.warning("agent_usage_missing", agent=run.agent, model=reply.model)
    state = replace(state, usage=state.usage.add(reply), turns=state.turns + 1)
    assistant = ChatMessage("assistant", reply.text or "", reply.tool_calls)
    if not reply.tool_calls:
        nudge = ChatMessage("user", f"error: finish by calling {run.submit.name}")
        return replace(state, history=(*state.history, assistant, nudge)), None
    state = replace(state, history=(*state.history, assistant))
    for call in reply.tool_calls:
        handle = _handle_submit if call.name == run.submit.name else _handle_tool
        state, outcome = await handle(run, state, call)
        if outcome is not None:
            return state, outcome
    return state, None


async def _handle_tool[T: BaseModel](run: AgentRun[T], state: RunState, call: ToolCall) -> Step[T]:
    """Run one tool within its call cap and fit its result to the context budget."""
    blocked = _secret_block(run.agent, call.name, json.dumps(call.arguments), state)
    if blocked is not None:
        return state, blocked
    if run.budget.cap_reached(call.name, state.counts):
        cap = run.budget.call_caps[call.name]
        text = f"error: {call.name!r} limit of {cap} reached; finish with what you have"
        return _with_tool_message(state, call, text), None
    try:
        result = await run.toolbox.call(call.name, call.arguments)
    except ToolUnavailable as error:
        return state, Unavailable(type(error).__name__, state.usage, state.turns)
    if result.ran:
        state = replace(state, counts=state.counts.plus(call.name))
    used = sum(estimate_tokens(message.content) for message in state.history)
    remaining = run.budget.context_tokens - used
    text = result_for_model(run.budget, call, result, remaining)
    return _with_tool_message(state, call, text), None


async def _handle_submit[T: BaseModel](
    run: AgentRun[T], state: RunState, call: ToolCall
) -> Step[T]:
    """Accept a valid submission for the current head, or allow one repair round."""
    checked = run.submit.check(call.arguments)
    if isinstance(checked, InvalidSubmission):
        if state.repaired:
            return state, CouldNotFinish("invalid_submission", state.usage, state.turns)
        state = replace(state, repaired=True, turn_limit=max(state.turn_limit, state.turns + 1))
        return _with_tool_message(state, call, checked.errors), None
    blocked = _secret_block(run.agent, call.name, checked.model_dump_json(), state)
    if blocked is not None:
        return state, blocked
    if isinstance(checked, ToneFiltered):
        checked = checked.filtered(filter_tone)
    try:
        current = await still_current(run.head_sha, run.read_current_head, agent=run.agent)
    except HeimdallError as error:
        return state, Unavailable(type(error).__name__, state.usage, state.turns)
    if not current:
        return state, Stale(state.usage, state.turns)
    return state, Completed(checked, state.usage, state.turns)


def _secret_block(agent: str, where: str, text: str, state: RunState) -> CouldNotFinish | None:
    """Fail the run when outgoing text holds a secret, logging only the kinds found.

    Args:
        agent: Agent name for the log line.
        where: Tool or submit name the text was bound for.
        text: The outgoing arguments or submission, serialised.
        state: The run so far, for the outcome's usage and turns.

    Returns:
        ``CouldNotFinish("secret_in_output")`` when a secret was found, otherwise None.
    """
    kinds = find_secret_kinds(text)
    if not kinds:
        return None
    log.warning("agent_secret_blocked", agent=agent, where=where, kinds=kinds)
    return CouldNotFinish("secret_in_output", state.usage, state.turns)


def _with_tool_message(state: RunState, call: ToolCall, text: str) -> RunState:
    """Return a new state with one tool result appended to the history."""
    message = ChatMessage("tool", text, tool_call_id=call.id)
    return replace(state, history=(*state.history, message))
