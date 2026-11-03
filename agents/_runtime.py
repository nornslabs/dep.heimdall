"""Shared agent plumbing: allowlisted tools, a bounded tool loop, usage totals, stale drops."""

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from heimdall.adapters.ai import ChatMessage, ChatResponse, ToolChoice, ToolSpec, Usage
from heimdall.modules.audit.api import UsageRecord
from heimdall.shared.errors import HeimdallError, ToolNotAllowed
from heimdall.shared.logging import get_logger

NO_USAGE = Usage(0, 0)

ToolHandler = Callable[[Mapping[str, object]], Awaitable[object]]

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
class AgentTool:
    """One tool an agent offers the model: its description and the code that runs it."""

    spec: ToolSpec
    handler: ToolHandler


class ToolBox:
    """The agent's allowed tools, as specs for the model and a guarded ``call``."""

    def __init__(self, allowed: frozenset[str], tools: Sequence[AgentTool]) -> None:
        """Bind handlers, refusing any tool that is not on the agent's allowlist.

        Args:
            allowed: Names from the agent's ``allowed_tools.py``.
            tools: Tool specs and handlers to offer.

        Raises:
            ToolNotAllowed: a tool is not in ``allowed``.
        """
        for tool in tools:
            if tool.spec.name not in allowed:
                raise ToolNotAllowed(f"tool {tool.spec.name!r} is not allowed for this agent")
        self._tools = {tool.spec.name: tool for tool in tools}

    def specs(self) -> tuple[ToolSpec, ...]:
        """Return the specs of every allowed tool, for ``ai.chat(..., tools=...)``."""
        return tuple(tool.spec for tool in self._tools.values())

    async def call(self, name: str, arguments: Mapping[str, object]) -> str:
        """Run one tool and return its JSON result, or an error string for the model.

        Args:
            name: Tool the model asked for.
            arguments: Parsed arguments from the model.

        Returns:
            JSON text of the result, or ``error: ...``. Error text names only the tool the
            model asked for or the exception type, never the exception message, which may
            carry untrusted content.
        """
        tool = self._tools.get(name)
        if tool is None:
            return f"error: tool {name!r} is not allowed"
        try:
            result = await tool.handler(arguments)
        except Exception as error:
            log.warning("agent_tool_failed", tool=name, error_type=type(error).__name__)
            return f"error: {type(error).__name__}"
        return json.dumps(result, default=str)


@dataclass(frozen=True)
class RunUsage:
    """Tokens summed over one agent run and the model the provider said answered."""

    usage: Usage = NO_USAGE
    model: str | None = None

    def add(self, reply: ChatResponse) -> "RunUsage":
        """Return new totals that include one more reply."""
        return RunUsage(self.usage + reply.usage, reply.model or self.model)

    def to_record(
        self, agent: str, pr_id: UUID | None, *, fallback_model: str = "unknown"
    ) -> UsageRecord:
        """Build the audit row for this run, using the configured model if none answered."""
        return UsageRecord(
            agent=agent,
            model=self.model or fallback_model,
            input_tokens=self.usage.input_tokens,
            output_tokens=self.usage.output_tokens,
            pr_id=pr_id,
        )


@dataclass(frozen=True)
class LoopResult:
    """The final text reply, the full history that led to it, and what it cost."""

    reply: ChatResponse
    messages: tuple[ChatMessage, ...]
    usage: RunUsage


class AgentTurnLimitError(HeimdallError):
    """The model kept calling tools past the agent's MAX_TURNS."""

    def __init__(self, max_turns: int, usage: RunUsage) -> None:
        """Keep the usage spent so the run can still be recorded.

        Args:
            max_turns: The limit that was reached.
            usage: Tokens spent before stopping.
        """
        super().__init__(f"agent stopped after {max_turns} model turns")
        self.usage = usage


async def run_tool_loop(
    ai: ChatModel,
    toolbox: ToolBox,
    messages: Sequence[ChatMessage],
    max_turns: int,
    usage: RunUsage | None = None,
) -> LoopResult:
    """Repeat chat, run the requested tools, append results, until a reply without tool calls.

    Args:
        ai: Model to call.
        toolbox: Allowed tools; unknown tool names come back to the model as errors.
        messages: History to start from; never modified.
        max_turns: Most model calls this loop may make.
        usage: Totals carried in from earlier calls in the same run; none when omitted.

    Returns:
        The final reply, the full history and the summed usage.

    Raises:
        AgentTurnLimitError: the model still wanted tools after ``max_turns`` calls.
    """
    history = tuple(messages)
    totals = usage if usage is not None else RunUsage()
    for _turn in range(max_turns):
        reply = await ai.chat(history, toolbox.specs())
        totals = totals.add(reply)
        if not reply.tool_calls:
            return LoopResult(reply, history, totals)
        history = (*history, ChatMessage("assistant", reply.text or "", reply.tool_calls))
        for call in reply.tool_calls:
            output = await toolbox.call(call.name, call.arguments)
            history = (*history, ChatMessage("tool", output, tool_call_id=call.id))
    raise AgentTurnLimitError(max_turns, totals)


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
