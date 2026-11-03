"""HTTP implementations for OpenAI-compatible and Anthropic APIs."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Protocol, cast

import httpx

from heimdall.adapters.ai.config import AiConfig
from heimdall.adapters.ai.errors import AiBadResponse, AiUnavailable
from heimdall.adapters.ai.types import (
    ChatMessage,
    ChatResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)


class Provider(Protocol):
    """One HTTP provider capable of translating the shared chat contract."""

    async def chat(
        self,
        client: httpx.AsyncClient,
        config: AiConfig,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
        tool_choice: str,
    ) -> ChatResponse:
        """Send one chat request and parse its provider response."""
        ...


class OpenAiCompatibleProvider:
    """Call OpenAI Chat Completions compatible endpoints."""

    async def chat(
        self,
        client: httpx.AsyncClient,
        config: AiConfig,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
        tool_choice: str,
    ) -> ChatResponse:
        """Translate a chat turn to the OpenAI-compatible wire format."""
        payload: dict[str, object] = {
            "model": config.model,
            "messages": [_openai_message(message) for message in messages],
            "max_tokens": config.max_tokens,
        }
        if tools:
            payload["tools"] = [_openai_tool(tool) for tool in tools]
            payload["tool_choice"] = tool_choice
        headers = _bearer_headers(config.api_key)
        response = await _post(client, config, "/chat/completions", payload, headers)
        body = _json_object(response)
        try:
            choices_value = body["choices"]
            if not isinstance(choices_value, list):
                raise ValueError
            choices = cast(list[object], choices_value)
            first = choices[0] if choices else None
            first_object = cast(dict[str, object], first) if isinstance(first, dict) else None
            message = first_object.get("message") if first_object is not None else None
            if not isinstance(message, dict):
                raise ValueError
            message_object = cast(dict[str, object], message)
            text = message_object.get("content")
            if text is None:
                text = ""
            if not isinstance(text, str):
                raise ValueError
            calls = _openai_tool_calls(message_object.get("tool_calls", []))
            usage = body.get("usage", {})
            if not isinstance(usage, dict):
                raise ValueError
            usage_object = cast(dict[str, object], usage)
            return ChatResponse(
                text=text,
                tool_calls=calls,
                usage=TokenUsage(
                    _required_nonnegative_int(usage_object.get("prompt_tokens", 0)),
                    _required_nonnegative_int(usage_object.get("completion_tokens", 0)),
                ),
                model=_optional_string(body.get("model"), config.model),
            )
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise AiBadResponse("OpenAI-compatible response was malformed") from error


class AnthropicProvider:
    """Call Anthropic's native Messages API."""

    async def chat(
        self,
        client: httpx.AsyncClient,
        config: AiConfig,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
        tool_choice: str,
    ) -> ChatResponse:
        """Translate a chat turn to Anthropic's Messages wire format."""
        system = "\n\n".join(message.content for message in messages if message.role == "system")
        payload: dict[str, object] = {
            "model": config.model,
            "messages": _anthropic_messages(messages),
            "max_tokens": config.max_tokens,
        }
        if system:
            payload["system"] = system
        if tools and tool_choice != "none":
            payload["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.parameters,
                }
                for tool in tools
            ]
            if tool_choice == "required":
                payload["tool_choice"] = {"type": "any"}
            else:
                payload["tool_choice"] = {"type": "auto"}
        headers = {"anthropic-version": "2023-06-01"}
        if config.api_key:
            headers["x-api-key"] = config.api_key
        response = await _post(client, config, "/v1/messages", payload, headers)
        body = _json_object(response)
        try:
            content = body["content"]
            if not isinstance(content, list):
                raise ValueError
            content_items = cast(list[object], content)
            text_parts: list[str] = []
            calls: list[ToolCall] = []
            for item in content_items:
                if not isinstance(item, dict):
                    raise ValueError
                block = cast(dict[str, object], item)
                if block.get("type") == "text":
                    text_value = block.get("text")
                    if not isinstance(text_value, str):
                        raise ValueError
                    text_parts.append(text_value)
                elif block.get("type") == "tool_use":
                    calls.append(_anthropic_tool_call(block))
            usage = body.get("usage", {})
            if not isinstance(usage, dict):
                raise ValueError
            usage_object = cast(dict[str, object], usage)
            return ChatResponse(
                text="\n".join(text_parts),
                tool_calls=tuple(calls),
                usage=TokenUsage(
                    _required_nonnegative_int(usage_object.get("input_tokens", 0)),
                    _required_nonnegative_int(usage_object.get("output_tokens", 0)),
                ),
                model=_optional_string(body.get("model"), config.model),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise AiBadResponse("Anthropic response was malformed") from error


async def _post(
    client: httpx.AsyncClient,
    config: AiConfig,
    path: str,
    payload: dict[str, object],
    headers: dict[str, str],
) -> httpx.Response:
    """Send a request and distinguish retryable transport failures."""
    try:
        response = await client.post(
            f"{config.base_url.rstrip('/')}{path}",
            json=payload,
            headers=headers,
            timeout=config.timeout_seconds,
        )
    except httpx.HTTPError as error:
        raise AiUnavailable("AI provider request failed") from error
    if response.status_code == 429 or response.status_code >= 500:
        raise AiUnavailable(f"AI provider returned HTTP {response.status_code}")
    if not response.is_success:
        raise AiBadResponse(f"AI provider rejected request with HTTP {response.status_code}")
    return response


def _json_object(response: httpx.Response) -> dict[str, object]:
    """Require a JSON object response without exposing provider bodies."""
    try:
        value = response.json()
    except ValueError as error:
        raise AiBadResponse("AI provider response was not JSON") from error
    if not isinstance(value, dict):
        raise AiBadResponse("AI provider response was not an object")
    return cast(dict[str, object], value)


def _openai_message(message: ChatMessage) -> dict[str, object]:
    """Encode one provider-neutral message for Chat Completions."""
    result: dict[str, object] = {"role": message.role, "content": message.content}
    if message.tool_call_id:
        result["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        result["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, separators=(",", ":")),
                },
            }
            for call in message.tool_calls
        ]
    return result


def _openai_tool(tool: ToolDefinition) -> dict[str, object]:
    """Encode a shared tool definition in Chat Completions format."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def _openai_tool_calls(value: object) -> tuple[ToolCall, ...]:
    """Parse function calls and reject invalid JSON arguments."""
    if not isinstance(value, list):
        raise ValueError
    calls: list[ToolCall] = []
    for raw_item in cast(list[object], value):
        if not isinstance(raw_item, dict):
            raise ValueError
        item = cast(dict[str, object], raw_item)
        function = item.get("function")
        if not isinstance(function, dict):
            raise ValueError
        function_object = cast(dict[str, object], function)
        call_id = item.get("id")
        name = function_object.get("name")
        arguments_raw = function_object.get("arguments")
        if not isinstance(call_id, str) or not isinstance(name, str):
            raise ValueError
        arguments = json.loads(arguments_raw) if isinstance(arguments_raw, str) else arguments_raw
        if not isinstance(arguments, dict):
            raise ValueError
        calls.append(ToolCall(call_id, name, cast(dict[str, object], arguments)))
    return tuple(calls)


def _anthropic_messages(messages: Sequence[ChatMessage]) -> list[dict[str, object]]:
    """Encode user and assistant messages, including tool results."""
    result: list[dict[str, object]] = []
    for message in messages:
        if message.role == "system":
            continue
        if message.role == "tool":
            if not message.tool_call_id:
                raise AiBadResponse("tool result is missing its call id")
            result.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.tool_call_id,
                            "content": message.content,
                        }
                    ],
                }
            )
            continue
        content: list[dict[str, object]] = []
        if message.content:
            content.append({"type": "text", "text": message.content})
        if message.role == "assistant":
            content.extend(
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                for call in message.tool_calls
            )
        result.append({"role": message.role, "content": content or ""})
    return result


def _anthropic_tool_call(block: dict[str, object]) -> ToolCall:
    """Validate one Anthropic tool-use content block."""
    call_id = block.get("id")
    name = block.get("name")
    arguments = block.get("input")
    if not isinstance(call_id, str) or not isinstance(name, str) or not isinstance(arguments, dict):
        raise ValueError
    return ToolCall(call_id, name, cast(dict[str, object], arguments))


def _bearer_headers(api_key: str | None) -> dict[str, str]:
    """Build authorization headers only when a key is configured."""
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _required_nonnegative_int(value: object) -> int:
    """Validate token usage counts without accepting booleans."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError
    return value


def _optional_string(value: object, fallback: str) -> str:
    """Use the configured model when a provider omits model metadata."""
    if value is None:
        return fallback
    if not isinstance(value, str) or not value:
        raise ValueError
    return value
