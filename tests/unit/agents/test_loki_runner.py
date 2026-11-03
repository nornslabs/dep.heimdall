"""Loki's review runner sends review prompts through the AI HTTP adapter."""

import json

import httpx
import pytest

from agents.loki.runner import AiReviewRunner
from heimdall.adapters.ai import AiAdapter, AiConfig


@pytest.mark.asyncio
async def test_review_prompt_is_sent_as_provider_message() -> None:
    """The model receives the trust-marked review prompt as user content."""
    captured: dict[str, object] = {}

    async def respond(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"findings": []}'}}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 2},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    runner = AiReviewRunner(
        AiAdapter(AiConfig("openai_compat", "https://llm.example/v1", None, "reviewer"), client)
    )
    result = await runner.review("<untrusted_pr_data>example</untrusted_pr_data>")
    await runner.aclose()
    assert result == '{"findings": []}'
    messages = captured["messages"]
    assert isinstance(messages, list)
    assert messages[0]["content"] == "<untrusted_pr_data>example</untrusted_pr_data>"
