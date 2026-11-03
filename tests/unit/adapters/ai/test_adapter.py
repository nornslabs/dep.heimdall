"""AI providers normalize HTTP text, tool calls, and retryable failures."""

import httpx
import pytest
import respx

from agents._runtime import ToolBox, run_tool_loop
from heimdall.adapters.ai import (
    AiAdapter,
    AiBadResponse,
    AiConfig,
    ChatMessage,
    ToolDefinition,
)

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


@pytest.mark.asyncio
@respx.mock
async def test_openai_compatible_text_and_usage() -> None:
    """Normalize OpenAI-compatible text and token usage."""
    route = respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "gpt-test",
                "choices": [{"message": {"content": "ready"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            },
        )
    )
    async with AiAdapter(
        AiConfig("openai_compat", "https://api.openai.com/v1", "key", "gpt-test")
    ) as ai:
        response = await ai.chat((ChatMessage("user", "status"),))
    assert route.called
    assert response.text == "ready"
    assert (response.input_tokens, response.output_tokens) == (12, 4)


@pytest.mark.asyncio
@respx.mock
async def test_openai_compatible_tool_call_is_parsed() -> None:
    """Parse provider function calls into typed arguments."""
    respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {
                                        "name": "get_repro",
                                        "arguments": '{"run_id":"r1"}',
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        )
    )
    tool = ToolDefinition("get_repro", "Read a run", {"type": "object"})
    async with AiAdapter(
        AiConfig("openai_compat", "https://api.openai.com/v1", None, "test")
    ) as ai:
        response = await ai.chat((ChatMessage("user", "read"),), (tool,))
    assert response.tool_calls[0].name == "get_repro"
    assert response.tool_calls[0].arguments == {"run_id": "r1"}


@pytest.mark.asyncio
@respx.mock
async def test_openai_compatible_rejects_malformed_tool_arguments() -> None:
    """Reject invalid JSON tool arguments as a bad model response."""
    respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {"name": "get_repro", "arguments": "not-json"},
                                }
                            ],
                        }
                    }
                ],
                "usage": {},
            },
        )
    )
    async with AiAdapter(
        AiConfig("openai_compat", "https://api.openai.com/v1", None, "test")
    ) as ai:
        with pytest.raises(AiBadResponse, match="response was malformed"):
            await ai.chat((ChatMessage("user", "read"),))


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_text_and_tool_call_are_normalized() -> None:
    """Translate Anthropic's native response blocks to shared types."""
    route = respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "claude-test",
                "content": [
                    {"type": "text", "text": "checking"},
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "get_repro",
                        "input": {"run_id": "r1"},
                    },
                ],
                "usage": {"input_tokens": 8, "output_tokens": 6},
            },
        )
    )
    tool = ToolDefinition("get_repro", "Read a run", {"type": "object"})
    async with AiAdapter(
        AiConfig("anthropic", "https://api.anthropic.com", "key", "claude-test")
    ) as ai:
        response = await ai.chat((ChatMessage("user", "read"),), (tool,))
    assert route.called
    assert response.text == "checking"
    assert response.tool_calls[0].arguments == {"run_id": "r1"}


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_rejects_malformed_tool_arguments() -> None:
    """Reject non-object Anthropic tool inputs as malformed responses."""
    respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [{"type": "tool_use", "id": "tool-1", "name": "get_repro", "input": []}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )
    )
    async with AiAdapter(
        AiConfig("anthropic", "https://api.anthropic.com", None, "claude-test")
    ) as ai:
        with pytest.raises(AiBadResponse, match="response was malformed"):
            await ai.chat((ChatMessage("user", "read"),))


@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_retries_and_sends_one_notice() -> None:
    """Retry HTTP 429 through the shared runtime and send one notice."""
    route = respx.post(OPENAI_URL).mock(
        side_effect=[
            httpx.Response(429, json={"error": "busy"}),
            httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "recovered"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            ),
        ]
    )
    notices: list[str] = []

    async def notice(message: str) -> None:
        notices.append(message)

    async def no_wait(_delay: float) -> None:
        return None

    async with AiAdapter(
        AiConfig("openai_compat", "https://api.openai.com/v1", None, "test")
    ) as ai:
        result = await run_tool_loop(
            ai,
            ToolBox((), {}, frozenset()),
            (ChatMessage("user", "status"),),
            on_notice=notice,
            sleep=no_wait,
        )
    assert result.text == "recovered"
    assert route.call_count == 2
    assert len(notices) == 1


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_rate_limit_retries() -> None:
    """Retry Anthropic HTTP 429 using the same shared runtime policy."""
    route = respx.post(ANTHROPIC_URL).mock(
        side_effect=[
            httpx.Response(429, json={"error": "busy"}),
            httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": "recovered"}],
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            ),
        ]
    )

    async def no_wait(_delay: float) -> None:
        return None

    async with AiAdapter(
        AiConfig("anthropic", "https://api.anthropic.com", None, "claude-test")
    ) as ai:
        result = await run_tool_loop(
            ai,
            ToolBox((), {}, frozenset()),
            (ChatMessage("user", "status"),),
            sleep=no_wait,
        )
    assert result.text == "recovered"
    assert route.call_count == 2
