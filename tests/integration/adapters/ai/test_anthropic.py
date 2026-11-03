"""The native Anthropic provider maps Messages API blocks to the adapter's types."""

import json
from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from pydantic import SecretStr

from heimdall.adapters.ai import AiAdapter, AiConfig, ChatMessage, ToolCall, ToolSpec, Usage
from heimdall.shared.errors import AiBadResponse

BASE_URL = "https://anthropic.test"
MESSAGES = f"{BASE_URL}/v1/messages"
DIFF_TOOL = ToolSpec("get_diff", "Read the PR diff.", {"type": "object", "properties": {}})


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
async def ai() -> AsyncIterator[AiAdapter]:
    config = AiConfig(
        provider="anthropic",
        base_url=BASE_URL,
        api_key=SecretStr("ak-test"),
        model="native-test",
        max_output_tokens=512,
    )
    adapter = AiAdapter(config, sleep=_no_sleep)
    yield adapter
    await adapter.aclose()


def _reply(content: list[dict[str, object]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "native-test-2026",
            "content": content,
            "usage": {"input_tokens": 11, "output_tokens": 5},
        },
    )


@respx.mock
async def test_text_reply_and_top_level_system(ai: AiAdapter) -> None:
    route = respx.post(MESSAGES).mock(return_value=_reply([{"type": "text", "text": "hi"}]))

    reply = await ai.chat([ChatMessage("system", "rules"), ChatMessage("user", "hello")])

    assert reply.text == "hi"
    assert reply.usage == Usage(11, 5)
    assert reply.model == "native-test-2026"
    request = route.calls.last.request
    assert request.headers["x-api-key"] == "ak-test"
    assert request.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(request.content)
    assert body["system"] == "rules"
    assert body["max_tokens"] == 512
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert "tools" not in body


@respx.mock
async def test_tool_use_block_becomes_tool_call(ai: AiAdapter) -> None:
    route = respx.post(MESSAGES).mock(
        return_value=_reply(
            [
                {"type": "text", "text": "Reading the diff."},
                {"type": "tool_use", "id": "tu_1", "name": "get_diff", "input": {"pr": 7}},
            ]
        )
    )

    reply = await ai.chat([ChatMessage("user", "review")], [DIFF_TOOL])

    assert reply.text == "Reading the diff."
    assert reply.tool_calls == (ToolCall("tu_1", "get_diff", {"pr": 7}),)
    body = json.loads(route.calls.last.request.content)
    assert body["tools"] == [
        {
            "name": "get_diff",
            "description": "Read the PR diff.",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]
    assert body["tool_choice"] == {"type": "auto"}


@respx.mock
async def test_tool_result_round_trip_uses_content_blocks(ai: AiAdapter) -> None:
    route = respx.post(MESSAGES).mock(return_value=_reply([{"type": "text", "text": "done"}]))
    call = ToolCall("tu_1", "get_diff", {"pr": 7})

    await ai.chat(
        [
            ChatMessage("user", "review"),
            ChatMessage("assistant", "Reading.", tool_calls=(call,)),
            ChatMessage("tool", "diff text", tool_call_id="tu_1"),
            ChatMessage("tool", "more text", tool_call_id="tu_2"),
        ],
        [DIFF_TOOL],
    )

    sent = json.loads(route.calls.last.request.content)["messages"]
    assert sent[1] == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Reading."},
            {"type": "tool_use", "id": "tu_1", "name": "get_diff", "input": {"pr": 7}},
        ],
    }
    assert sent[2] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "diff text"},
            {"type": "tool_result", "tool_use_id": "tu_2", "content": "more text"},
        ],
    }


@respx.mock
async def test_reply_stopped_at_max_tokens_raises_bad_response(ai: AiAdapter) -> None:
    respx.post(MESSAGES).mock(
        return_value=httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "{"}], "stop_reason": "max_tokens"},
        )
    )

    with pytest.raises(AiBadResponse, match="truncated"):
        await ai.chat([ChatMessage("user", "hello")])
