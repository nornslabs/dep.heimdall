"""The agent's allowed tools: allow-list, JSON-schema argument checks and outage retries.

Every refusal comes back to the model as an ``error: ...`` result, so nothing outside the
agent's list runs and no malformed argument ever reaches the tool server.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from jsonschema import Draft202012Validator
from jsonschema.protocols import Validator

from heimdall.adapters.ai import ToolSpec
from heimdall.shared.errors import HeimdallError, ToolNotAllowed, ToolUnavailable
from heimdall.shared.logging import get_logger

TOOL_RETRY_DELAYS_SECONDS = (1.0, 4.0)
MAX_ARGUMENT_ERRORS = 3

ToolHandler = Callable[[Mapping[str, object]], Awaitable[object]]
Sleep = Callable[[float], Awaitable[None]]

log = get_logger(__name__)


@dataclass(frozen=True)
class AgentTool:
    """One tool an agent offers the model: its description and the code that runs it."""

    spec: ToolSpec
    handler: ToolHandler


@dataclass(frozen=True)
class ToolResult:
    """What a tool call gave the model, and whether the tool actually ran (for call caps).

    Attributes:
        text: The result, or an ``error: ...`` line written by the toolbox.
        ran: True once the handler was called, so the call counts against its cap.
        failed: True when the handler raised, so ``text`` is the toolbox's own error line
            rather than tool output, and is not wrapped as PR data.
    """

    text: str
    ran: bool
    failed: bool = False


class ToolBox:
    """The agent's allowed tools, as specs for the model and a guarded ``call``."""

    def __init__(
        self,
        allowed: frozenset[str],
        tools: Sequence[AgentTool],
        *,
        sleep: Sleep = asyncio.sleep,
        retry_delays: tuple[float, ...] = TOOL_RETRY_DELAYS_SECONDS,
    ) -> None:
        """Bind handlers and argument validators, refusing any tool off the allow-list.

        Args:
            allowed: Names from the agent's ``allowed_tools.py``.
            tools: Tool specs and handlers to offer.
            sleep: Async sleep between outage retries; injectable so tests do not wait.
            retry_delays: Wait before each retry of a tool that reported an outage.

        Raises:
            ToolNotAllowed: a tool is not in ``allowed``.
            jsonschema.SchemaError: a tool's input schema is not valid JSON Schema.
        """
        for tool in tools:
            if tool.spec.name not in allowed:
                raise ToolNotAllowed(f"tool {tool.spec.name!r} is not allowed for this agent")
            Draft202012Validator.check_schema(dict(tool.spec.input_schema))
        self._tools = {tool.spec.name: tool for tool in tools}
        self._validators: dict[str, Validator] = {
            tool.spec.name: Draft202012Validator(dict(tool.spec.input_schema)) for tool in tools
        }
        self._sleep = sleep
        self._retry_delays = retry_delays

    def specs(self) -> tuple[ToolSpec, ...]:
        """Return the specs of every allowed tool, for ``ai.chat(..., tools=...)``."""
        return tuple(tool.spec for tool in self._tools.values())

    async def call(self, name: str, arguments: Mapping[str, object]) -> ToolResult:
        """Run one tool and return its result text, or an error text for the model.

        Args:
            name: Tool the model asked for.
            arguments: Parsed arguments from the model.

        Returns:
            The result (plain text as-is, anything else as JSON) or ``error: ...``. Error
            text names only the tool, the argument path or the exception type, never the
            exception message, which may carry untrusted content.

        Raises:
            ToolUnavailable: the tool reported an outage on every attempt.
        """
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(f"error: tool {name!r} is not allowed", ran=False)
        refusal = self._argument_errors(name, arguments)
        if refusal is not None:
            return ToolResult(refusal, ran=False)
        try:
            result = await self._run_with_retry(tool, arguments)
        except ToolUnavailable:
            raise
        except Exception as error:
            log.warning("agent_tool_failed", tool=name, error_type=type(error).__name__)
            return ToolResult(f"error: {type(error).__name__}", ran=True, failed=True)
        return ToolResult(_as_text(result), ran=True)

    def _argument_errors(self, name: str, arguments: Mapping[str, object]) -> str | None:
        """Describe the first few schema violations, or None when the arguments are valid."""
        # The model's arguments are parsed JSON, so they are JSON values whatever their
        # static type says.
        instance = cast(Mapping[str, Any], arguments)
        errors = sorted(self._validators[name].iter_errors(instance), key=lambda e: e.json_path)
        if not errors:
            return None
        details = "; ".join(f"{e.json_path}: {e.message}" for e in errors[:MAX_ARGUMENT_ERRORS])
        return f"error: invalid arguments for {name!r}: {details}"

    async def _run_with_retry(self, tool: AgentTool, arguments: Mapping[str, object]) -> object:
        """Run the handler, retrying only errors marked retryable, then give up as unavailable.

        Raises:
            ToolUnavailable: every attempt failed with a retryable error.
        """
        name = tool.spec.name
        for attempt, delay in enumerate(self._retry_delays, start=1):
            try:
                return await tool.handler(arguments)
            except HeimdallError as error:
                if not error.retryable:
                    raise
                log.warning("agent_tool_retry", tool=name, attempt=attempt, wait_seconds=delay)
                await self._sleep(delay)
        try:
            return await tool.handler(arguments)
        except HeimdallError as error:
            if error.retryable:
                raise ToolUnavailable(f"tool {name!r} stayed unavailable") from error
            raise


def _as_text(result: object) -> str:
    """Pass text through untouched so its lines survive truncation; encode the rest as JSON."""
    if isinstance(result, str):
        return result
    return json.dumps(result, default=str)
