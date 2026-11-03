"""The OpenAI-compatible provider maps chat, tool calls, usage and errors over plain HTTP."""

import json
from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from pydantic import SecretStr

from heimdall.adapters.ai import AiAdapter, AiConfig, ChatMessage, ToolCall, ToolSpec, Usage
from heimdall.shared.errors import AiBadResponse, AiRequestFailed, AiUnavailable

BASE_URL = "https://llm.test/v1"
COMPLETIONS = f"{BASE_URL}/chat/completions"
DIFF_TOOL = ToolSpec("get_diff", "Read the PR diff.", {"type": "object", "properties": {}})


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
async def ai() -> AsyncIterator[AiAdapter]:
    config = AiConfig(base_url=BASE_URL, api_key=SecretStr("sk-test"), model="gpt-test")
    adapter = AiAdapter(config, sleep=_no_sleep)
    yield adapter
    await adapter.aclose()


def _reply(message: dict[str, object], usage: dict[str, int] | None = None) -> httpx.Response:
    body: dict[str, object] = {"model": "gpt-test-2026", "choices": [{"message": message}]}
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)


@respx.mock
async def test_text_reply_returns_text_usage_and_model(ai: AiAdapter) -> None:
    route = respx.post(COMPLETIONS).mock(
        return_value=_reply(
            {"role": "assistant", "content": "hi"}, {"prompt_tokens": 7, "completion_tokens": 2}
        )
    )

    reply = await ai.chat([ChatMessage("system", "rules"), ChatMessage("user", "hello")])

    assert reply.text == "hi"
    assert reply.tool_calls == ()
    assert reply.usage == Usage(7, 2)
    assert reply.model == "gpt-test-2026"
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer sk-test"
    body = json.loads(request.content)
    assert body["model"] == "gpt-test"
    assert body["messages"] == [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "hello"},
    ]
    assert "tools" not in body
    assert "tool_choice" not in body


@respx.mock
async def test_tool_call_reply_parses_arguments_and_sends_tool_specs(ai: AiAdapter) -> None:
    route = respx.post(COMPLETIONS).mock(
        return_value=_reply(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_diff", "arguments": '{"pr_number": 7}'},
                    }
                ],
            }
        )
    )

    reply = await ai.chat([ChatMessage("user", "review")], [DIFF_TOOL], tool_choice="required")

    assert reply.text is None
    assert reply.tool_calls == (ToolCall("call_1", "get_diff", {"pr_number": 7}),)
    body = json.loads(route.calls.last.request.content)
    assert body["tool_choice"] == "required"
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "get_diff",
                "description": "Read the PR diff.",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


@respx.mock
async def test_tool_history_is_sent_back_in_openai_shape(ai: AiAdapter) -> None:
    route = respx.post(COMPLETIONS).mock(return_value=_reply({"content": "done"}))
    call = ToolCall("call_1", "get_diff", {"pr_number": 7})

    await ai.chat(
        [
            ChatMessage("user", "review"),
            ChatMessage("assistant", "", tool_calls=(call,)),
            ChatMessage("tool", "diff text", tool_call_id="call_1"),
        ]
    )

    sent = json.loads(route.calls.last.request.content)["messages"]
    assert sent[1] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_diff", "arguments": '{"pr_number": 7}'},
            }
        ],
    }
    assert sent[2] == {"role": "tool", "tool_call_id": "call_1", "content": "diff text"}


@respx.mock
async def test_malformed_tool_arguments_raise_bad_response(ai: AiAdapter) -> None:
    respx.post(COMPLETIONS).mock(
        return_value=_reply(
            {
                "tool_calls": [
                    {"id": "c", "function": {"name": "get_diff", "arguments": "{not json"}}
                ]
            }
        )
    )

    with pytest.raises(AiBadResponse, match="arguments"):
        await ai.chat([ChatMessage("user", "review")], [DIFF_TOOL])


@respx.mock
async def test_reply_without_text_or_tool_calls_raises_bad_response(ai: AiAdapter) -> None:
    respx.post(COMPLETIONS).mock(return_value=_reply({"content": None}))

    with pytest.raises(AiBadResponse):
        await ai.chat([ChatMessage("user", "hello")])


@respx.mock
async def test_rate_limit_then_success_retries(ai: AiAdapter) -> None:
    route = respx.post(COMPLETIONS).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            _reply({"content": "ok"}),
        ]
    )

    reply = await ai.chat([ChatMessage("user", "hello")])

    assert reply.text == "ok"
    assert route.call_count == 2


@respx.mock
async def test_repeated_server_errors_raise_unavailable(ai: AiAdapter) -> None:
    route = respx.post(COMPLETIONS).mock(return_value=httpx.Response(500))

    with pytest.raises(AiUnavailable):
        await ai.chat([ChatMessage("user", "hello")])

    assert route.call_count == 4


@respx.mock
async def test_client_error_is_not_retried(ai: AiAdapter) -> None:
    route = respx.post(COMPLETIONS).mock(return_value=httpx.Response(401))

    with pytest.raises(AiRequestFailed, match="401"):
        await ai.chat([ChatMessage("user", "hello")])

    assert route.call_count == 1


@respx.mock
async def test_network_error_raises_unavailable(ai: AiAdapter) -> None:
    respx.post(COMPLETIONS).mock(side_effect=httpx.ConnectError("down"))

    with pytest.raises(AiUnavailable):
        await ai.chat([ChatMessage("user", "hello")])


@respx.mock
async def test_missing_usage_counts_as_zero(ai: AiAdapter) -> None:
    respx.post(COMPLETIONS).mock(return_value=_reply({"content": "hi"}))

    reply = await ai.chat([ChatMessage("user", "hello")])

    assert reply.usage == Usage(0, 0)


@respx.mock
async def test_invalid_json_body_raises_bad_response(ai: AiAdapter) -> None:
    respx.post(COMPLETIONS).mock(return_value=httpx.Response(200, text="<html>"))

    with pytest.raises(AiBadResponse):
        await ai.chat([ChatMessage("user", "hello")])


@respx.mock
async def test_reply_cut_off_at_the_token_limit_raises_bad_response(ai: AiAdapter) -> None:
    respx.post(COMPLETIONS).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"findings": ['}, "finish_reason": "length"}]
            },
        )
    )

    with pytest.raises(AiBadResponse, match="truncated"):
        await ai.chat([ChatMessage("user", "hello")])


@respx.mock
async def test_non_numeric_retry_after_falls_back_to_backoff() -> None:
    waits: list[float] = []

    async def record(seconds: float) -> None:
        waits.append(seconds)

    respx.post(COMPLETIONS).mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "nan"}), _reply({"content": "ok"})]
    )
    config = AiConfig(base_url=BASE_URL, api_key=SecretStr("k"), model="m")
    adapter = AiAdapter(config, sleep=record)

    reply = await adapter.chat([ChatMessage("user", "hello")])
    await adapter.aclose()

    assert reply.text == "ok"
    assert waits == [2.0]


@respx.mock
async def test_non_utf8_body_raises_bad_response(ai: AiAdapter) -> None:
    respx.post(COMPLETIONS).mock(return_value=httpx.Response(200, content=b"\xff\xfe\xfa"))

    with pytest.raises(AiBadResponse):
        await ai.chat([ChatMessage("user", "hello")])


@respx.mock
async def test_injected_client_still_gets_the_configured_timeout() -> None:
    route = respx.post(COMPLETIONS).mock(return_value=_reply({"content": "ok"}))
    config = AiConfig(base_url=BASE_URL, api_key=SecretStr("k"), model="m", timeout_s=7)
    async with httpx.AsyncClient() as http:
        await AiAdapter(config, http=http).chat([ChatMessage("user", "hi")])

    assert route.calls.last.request.extensions["timeout"]["read"] == 7
