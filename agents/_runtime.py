"""Shared bounded model and allowlisted-tool runtime for Heimdall agents."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.ai import AiBadResponse, AiUnavailable
from heimdall.adapters.ai.types import ChatMessage, ChatResponse, ToolDefinition
from heimdall.adapters.switch import RoomEventHandler, RoomPort, run_inbound_events
from heimdall.modules.audit.api import record_usage
from heimdall.shared.context import TenantCtx

MAX_AI_RETRIES = 2
MAX_TOOL_TURNS = 8
RETRY_DELAYS_SECONDS = (0.5, 1.0)
RETRY_NOTICE = "The model provider is temporarily unavailable; retrying."


class ToolHandler(Protocol):
    """Handle one validated, allowlisted tool call."""

    async def __call__(self, arguments: dict[str, object]) -> object:
        """Execute the tool and return JSON-serializable output."""
        ...


class McpSession(Protocol):
    """MCP client session used to list and invoke tools."""

    async def list_tools(self) -> object:
        """Return the server's registered tools."""
        ...

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        """Invoke one server tool."""
        ...


class ToolBox:
    """Expose only explicitly allowed tools to the model runtime."""

    def __init__(
        self,
        definitions: Sequence[ToolDefinition],
        handlers: Mapping[str, ToolHandler],
        allowed_tools: frozenset[str],
    ) -> None:
        """Validate the tool definitions and handlers against one agent allowlist."""
        names = {definition.name for definition in definitions}
        if len(names) != len(definitions) or names != set(handlers) or not names <= allowed_tools:
            raise ValueError("toolbox definitions, handlers, and allowlist do not match")
        self._definitions = tuple(definitions)
        self._handlers = dict(handlers)

    @classmethod
    async def from_mcp(
        cls,
        session: McpSession,
        allowed_tools: frozenset[str],
        installation_id: int,
    ) -> ToolBox:
        """Build a tenant-bound toolbox from an agent's scoped MCP session."""
        if installation_id <= 0:
            raise ValueError("installation_id must be positive")
        response = await session.list_tools()
        response_tools = getattr(response, "tools", None)
        if not isinstance(response_tools, list | tuple):
            raise AiBadResponse("MCP server returned an invalid tool list")
        definitions: list[ToolDefinition] = []
        handlers: dict[str, ToolHandler] = {}
        for tool in cast(list[object] | tuple[object, ...], response_tools):
            name = getattr(tool, "name", None)
            if not isinstance(name, str) or name not in allowed_tools:
                continue
            description = getattr(tool, "description", "")
            schema = getattr(tool, "inputSchema", None)
            if not isinstance(description, str) or not isinstance(schema, dict):
                raise AiBadResponse("MCP server returned an invalid tool definition")
            definitions.append(
                ToolDefinition(
                    name,
                    description,
                    _tenant_hidden_schema(cast(dict[str, object], schema)),
                )
            )
            handlers[name] = _mcp_handler(session, name, installation_id)
        if {item.name for item in definitions} != set(allowed_tools):
            raise AiBadResponse("MCP session does not expose the full agent allowlist")
        return cls(definitions, handlers, allowed_tools)

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        """Return only the schemas approved for this agent."""
        return self._definitions

    async def call(self, name: str, arguments: dict[str, object]) -> object:
        """Run one tool only when it was included in this agent's toolbox."""
        handler = self._handlers.get(name)
        if handler is None:
            raise AiBadResponse("model requested a tool outside the agent allowlist")
        return await handler(arguments)


def _tenant_hidden_schema(schema: dict[str, object]) -> dict[str, object]:
    """Hide installation_id from the model because runtime injects the tenant."""
    visible = dict(schema)
    properties = visible.get("properties")
    if isinstance(properties, dict):
        visible["properties"] = {
            key: value
            for key, value in cast(dict[str, object], properties).items()
            if key != "installation_id"
        }
    required = visible.get("required")
    if isinstance(required, list):
        visible["required"] = [
            required_name
            for required_name in cast(list[object], required)
            if isinstance(required_name, str) and required_name != "installation_id"
        ]
    return visible


def _mcp_handler(session: McpSession, name: str, installation_id: int) -> ToolHandler:
    """Bind one allowed MCP tool to the current tenant id."""

    async def call(arguments: dict[str, object]) -> object:
        if "installation_id" in arguments:
            raise AiBadResponse("model supplied a tenant identifier")
        result = await session.call_tool(
            name,
            {**arguments, "installation_id": installation_id},
        )
        if getattr(result, "isError", False) is True:
            raise AiBadResponse(f"MCP tool {name} returned an error")
        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, dict):
            return cast(dict[str, object], structured)
        content = getattr(result, "content", None)
        if not isinstance(content, list | tuple):
            raise AiBadResponse(f"MCP tool {name} returned no result")
        text_items: list[str] = []
        for item in cast(list[object] | tuple[object, ...], content):
            text = getattr(item, "text", None)
            if isinstance(text, str):
                text_items.append(text)
        if not text_items:
            raise AiBadResponse(f"MCP tool {name} returned no text result")
        if len(text_items) == 1:
            try:
                return json.loads(text_items[0])
            except json.JSONDecodeError:
                return {"text": text_items[0]}
        return {"text": "\n".join(text_items)}

    return call


class ModelClient(Protocol):
    """Minimal AI adapter contract needed by the runtime."""

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition] = (),
        tool_choice: str = "auto",
    ) -> ChatResponse:
        """Request one model turn."""
        ...


@dataclass(frozen=True)
class ToolLoopResult:
    """Final model text and whether a changed PR head stopped the run."""

    text: str
    turns: int
    stale: bool
    messages: tuple[ChatMessage, ...]


UsageRecorder = Callable[[ChatResponse], Awaitable[None]]
CurrentHeadCheck = Callable[[], Awaitable[bool]]
Notice = Callable[[str], Awaitable[None]]
Sleep = Callable[[float], Awaitable[None]]


async def run_agent_wakeups(
    room: RoomPort,
    handler: RoomEventHandler,
    stop: asyncio.Event,
) -> None:
    """Wake an agent handler for inbound room events until the worker stops."""
    await run_inbound_events(room, handler, stop)


def make_usage_recorder(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    pr_id: UUID | None,
    agent: str,
) -> UsageRecorder:
    """Bind model token recording to one tenant, PR, and agent."""
    if not agent.strip():
        raise ValueError("agent name is required")

    async def record(response: ChatResponse) -> None:
        await record_usage(
            sessions,
            ctx,
            pr_id,
            agent,
            response.model,
            response.input_tokens,
            response.output_tokens,
        )

    return record


async def run_tool_loop(
    ai: ModelClient,
    toolbox: ToolBox,
    messages: Sequence[ChatMessage],
    *,
    max_turns: int = MAX_TOOL_TURNS,
    is_current: CurrentHeadCheck | None = None,
    record_usage: UsageRecorder | None = None,
    on_notice: Notice | None = None,
    sleep: Sleep = asyncio.sleep,
) -> ToolLoopResult:
    """Run bounded model/tool turns, dropping stale work and retrying outages."""
    if max_turns <= 0:
        raise ValueError("max_turns must be positive")
    conversation = list(messages)
    notice_sent = False

    async def notify_once(message: str) -> None:
        nonlocal notice_sent
        if on_notice is not None and not notice_sent:
            await on_notice(message)
            notice_sent = True

    for turn in range(1, max_turns + 1):
        if is_current is not None and not await is_current():
            return ToolLoopResult("", turn - 1, True, tuple(conversation))
        response = await _chat_with_retries(
            ai,
            conversation,
            toolbox.definitions,
            on_notice=notify_once if on_notice is not None else None,
            sleep=sleep,
        )
        if record_usage is not None:
            await record_usage(response)
        if not response.tool_calls:
            return ToolLoopResult(response.text, turn, False, tuple(conversation))
        conversation.append(ChatMessage("assistant", response.text, tool_calls=response.tool_calls))
        for call in response.tool_calls:
            if is_current is not None and not await is_current():
                return ToolLoopResult("", turn, True, tuple(conversation))
            output = await toolbox.call(call.name, call.arguments)
            try:
                content = json.dumps(output, ensure_ascii=False)
            except (TypeError, ValueError) as error:
                raise AiBadResponse("agent tool returned non-JSON output") from error
            conversation.append(ChatMessage("tool", content, tool_call_id=call.id))
    raise AiBadResponse("agent exceeded the configured tool-turn limit")


async def _chat_with_retries(
    ai: ModelClient,
    messages: Sequence[ChatMessage],
    tools: Sequence[ToolDefinition],
    *,
    on_notice: Notice | None,
    sleep: Sleep,
) -> ChatResponse:
    """Retry only temporary provider failures and emit at most one notice."""
    notice_sent = False
    for attempt in range(MAX_AI_RETRIES + 1):
        try:
            return await ai.chat(messages, tools=tools, tool_choice="auto")
        except AiUnavailable:
            if attempt >= MAX_AI_RETRIES:
                raise
            if not notice_sent and on_notice is not None:
                await on_notice(RETRY_NOTICE)
                notice_sent = True
            await sleep(RETRY_DELAYS_SECONDS[attempt])
    raise AiUnavailable("AI provider remained unavailable")
