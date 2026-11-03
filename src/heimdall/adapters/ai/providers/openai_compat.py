"""OpenAI-compatible Chat Completions: OpenAI, OpenRouter, Groq, Together, Gemini, Ollama, vLLM."""

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING

import httpx

from heimdall.adapters.ai.providers._wire import (
    as_list,
    as_object,
    post_json,
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


class OpenAiCompat:
    """Speaks ``POST {base_url}/chat/completions`` with a Bearer key."""

    async def send(
        self,
        http: httpx.AsyncClient,
        config: "AiConfig",
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
        tool_choice: ToolChoice,
    ) -> ChatResponse:
        """Send one chat completion request and parse the first choice.

        Args:
            http: Shared HTTP client.
            config: Base URL, key, model and limits.
            messages: Conversation history, oldest first.
            tools: Tools offered to the model; omitted from the body when empty.
            tool_choice: Forwarded as-is when tools are offered.

        Returns:
            The parsed reply.

        Raises:
            AiUnavailable: network error, timeout, 429 or 5xx.
            AiRequestFailed: any other 4xx.
            AiBadResponse: no choice, bad tool arguments, or neither text nor tool calls.
        """
        payload = await post_json(
            http,
            f"{config.base_url.rstrip('/')}/chat/completions",
            {"Authorization": f"Bearer {config.api_key.get_secret_value()}"},
            _request_body(config, messages, tools, tool_choice),
        )
        return _parse_reply(payload, config.model)


def _request_body(
    config: "AiConfig",
    messages: Sequence[ChatMessage],
    tools: Sequence[ToolSpec],
    tool_choice: ToolChoice,
) -> dict[str, object]:
    """Build the Chat Completions body, leaving tool fields out when no tools are offered."""
    body: dict[str, object] = {
        "model": config.model,
        "messages": [_wire_message(message) for message in messages],
        "temperature": config.temperature,
        "max_tokens": config.max_output_tokens,
    }
    if not tools:
        return body
    return {**body, "tools": [_wire_tool(tool) for tool in tools], "tool_choice": tool_choice}


def _wire_message(message: ChatMessage) -> dict[str, object]:
    """Translate one neutral message, including tool calls and tool results."""
    if message.role == "tool":
        return {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
    if message.role == "assistant" and message.tool_calls:
        return {
            "role": "assistant",
            "content": message.content,
            "tool_calls": [_wire_tool_call(call) for call in message.tool_calls],
        }
    return {"role": message.role, "content": message.content}


def _wire_tool_call(call: ToolCall) -> dict[str, object]:
    """Send a past tool call back with its arguments re-encoded as a JSON string."""
    return {
        "id": call.id,
        "type": "function",
        "function": {"name": call.name, "arguments": json.dumps(dict(call.arguments))},
    }


def _wire_tool(tool: ToolSpec) -> dict[str, object]:
    """Describe one tool in function-calling form."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": dict(tool.input_schema),
        },
    }


def _parse_reply(payload: dict[str, object], requested_model: str) -> ChatResponse:
    """Read text, tool calls and usage from the first choice."""
    choices = as_list(payload.get("choices"))
    choice = as_object(choices[0]) if choices else None
    message = as_object(choice.get("message")) if choice is not None else None
    if message is None:
        raise AiBadResponse("model reply has no message")
    content = message.get("content")
    text = content if isinstance(content, str) and content else None
    calls = tuple(_parse_tool_call(raw) for raw in as_list(message.get("tool_calls")))
    if text is None and not calls:
        raise AiBadResponse("model reply has neither text nor tool calls")
    usage = as_object(payload.get("usage"))
    return ChatResponse(
        text=text,
        tool_calls=calls,
        usage=Usage(token_count(usage, "prompt_tokens"), token_count(usage, "completion_tokens")),
        model=reply_model(payload, requested_model),
    )


def _parse_tool_call(raw: object) -> ToolCall:
    """Parse one tool call, whose arguments arrive as a JSON-encoded string."""
    call = as_object(raw)
    function = as_object(call.get("function")) if call is not None else None
    if call is None or function is None:
        raise AiBadResponse("model tool call is malformed")
    call_id = call.get("id")
    name = function.get("name")
    if not isinstance(call_id, str) or not isinstance(name, str):
        raise AiBadResponse("model tool call is missing its id or name")
    return ToolCall(call_id, name, _parse_arguments(function.get("arguments")))


def _parse_arguments(raw: object) -> dict[str, object]:
    """Decode tool arguments; some servers already send an object instead of a string."""
    decoded: object = raw
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as error:
            raise AiBadResponse("model tool call arguments are not valid JSON") from error
    arguments = as_object(decoded)
    if arguments is None:
        raise AiBadResponse("model tool call arguments are not a JSON object")
    return arguments
