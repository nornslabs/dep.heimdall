"""Native Anthropic Messages API: top-level system, content blocks, tool_use and tool_result."""

from collections.abc import Sequence
from typing import TYPE_CHECKING

import httpx

from heimdall.adapters.ai.providers._wire import (
    as_list,
    as_object,
    post_json,
    raise_if_truncated,
    reply_model,
    token_count,
)
from heimdall.adapters.ai.types import (
    ChatMessage,
    ChatResponse,
    ToolCall,
    ToolChoice,
    ToolSpec,
    Usage,
)
from heimdall.shared.errors import AiBadResponse

if TYPE_CHECKING:
    from heimdall.adapters.ai.config import AiConfig

ANTHROPIC_VERSION = "2023-06-01"
_TOOL_CHOICE: dict[ToolChoice, dict[str, str]] = {
    "auto": {"type": "auto"},
    "required": {"type": "any"},
    "none": {"type": "none"},
}


class AnthropicNative:
    """Speaks ``POST {base_url}/v1/messages`` with ``x-api-key``."""

    async def send(
        self,
        http: httpx.AsyncClient,
        config: "AiConfig",
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
        tool_choice: ToolChoice,
    ) -> ChatResponse:
        """Send one Messages request and parse its content blocks.

        Args:
            http: Shared HTTP client.
            config: Base URL, key, model and limits.
            messages: Conversation history; system turns move to the top-level field.
            tools: Tools offered to the model; omitted from the body when empty.
            tool_choice: Mapped to Anthropic's ``auto``, ``any`` or ``none``.

        Returns:
            The parsed reply.

        Raises:
            AiUnavailable: network error, timeout, 429 or 5xx.
            AiRequestFailed: any other 4xx.
            AiBadResponse: malformed blocks, or neither text nor tool calls.
        """
        payload = await post_json(
            http,
            f"{config.base_url.rstrip('/')}/v1/messages",
            {
                "x-api-key": config.api_key.get_secret_value(),
                "anthropic-version": ANTHROPIC_VERSION,
            },
            _request_body(config, messages, tools, tool_choice),
            config.timeout_s,
        )
        return _parse_reply(payload, config.model)


def _request_body(
    config: "AiConfig",
    messages: Sequence[ChatMessage],
    tools: Sequence[ToolSpec],
    tool_choice: ToolChoice,
) -> dict[str, object]:
    """Build the Messages body; ``max_tokens`` is required by the API."""
    body: dict[str, object] = {
        "model": config.model,
        "max_tokens": config.max_output_tokens,
        "temperature": config.temperature,
        "messages": _wire_messages(messages),
    }
    system = "\n\n".join(message.content for message in messages if message.role == "system")
    if system:
        body = {**body, "system": system}
    if not tools:
        return body
    return {
        **body,
        "tools": [_wire_tool(tool) for tool in tools],
        "tool_choice": _TOOL_CHOICE[tool_choice],
    }


def _wire_messages(messages: Sequence[ChatMessage]) -> list[dict[str, object]]:
    """Translate history, folding consecutive tool results into one user turn."""
    wired: list[dict[str, object]] = []
    results: list[dict[str, object]] = []
    for message in messages:
        if message.role == "tool":
            results.append(_tool_result(message))
            continue
        if results:
            wired.append({"role": "user", "content": results})
            results = []
        if message.role != "system":
            wired.append(_wire_message(message))
    if results:
        wired.append({"role": "user", "content": results})
    return wired


def _wire_message(message: ChatMessage) -> dict[str, object]:
    """Send a user or assistant turn; assistant tool calls become tool_use blocks."""
    if not message.tool_calls:
        return {"role": message.role, "content": message.content}
    blocks: list[dict[str, object]] = []
    if message.content:
        blocks.append({"type": "text", "text": message.content})
    for call in message.tool_calls:
        blocks.append(
            {"type": "tool_use", "id": call.id, "name": call.name, "input": dict(call.arguments)}
        )
    return {"role": "assistant", "content": blocks}


def _tool_result(message: ChatMessage) -> dict[str, object]:
    """Wrap one tool output as a tool_result block."""
    return {"type": "tool_result", "tool_use_id": message.tool_call_id, "content": message.content}


def _wire_tool(tool: ToolSpec) -> dict[str, object]:
    """Describe one tool in Messages API form."""
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": dict(tool.input_schema),
    }


def _parse_reply(payload: dict[str, object], requested_model: str) -> ChatResponse:
    """Join text blocks and collect tool_use blocks."""
    raise_if_truncated(payload.get("stop_reason"), "max_tokens")
    texts: list[str] = []
    calls: list[ToolCall] = []
    for raw in as_list(payload.get("content")):
        block = as_object(raw)
        if block is None:
            raise AiBadResponse("model reply has a malformed content block")
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            texts.append(str(block["text"]))
        elif block.get("type") == "tool_use":
            calls.append(_parse_tool_use(block))
    text = "".join(texts) or None
    if text is None and not calls:
        raise AiBadResponse("model reply has neither text nor tool calls")
    usage = as_object(payload.get("usage"))
    return ChatResponse(
        text=text,
        tool_calls=tuple(calls),
        usage=Usage(token_count(usage, "input_tokens"), token_count(usage, "output_tokens")),
        model=reply_model(payload, requested_model),
    )


def _parse_tool_use(block: dict[str, object]) -> ToolCall:
    """Read one tool_use block, whose input is already a JSON object."""
    call_id = block.get("id")
    name = block.get("name")
    arguments = as_object(block.get("input"))
    if not isinstance(call_id, str) or not isinstance(name, str) or arguments is None:
        raise AiBadResponse("model tool_use block is malformed")
    return ToolCall(call_id, name, arguments)
