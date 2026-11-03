"""Shared agent tool runtime enforces scopes, staleness, and bounded retries."""

from collections.abc import Sequence
from types import SimpleNamespace

import pytest

from agents._runtime import ToolBox, run_tool_loop
from heimdall.adapters.ai import (
    AiBadResponse,
    AiUnavailable,
    ChatMessage,
    ChatResponse,
    TokenUsage,
)
from heimdall.adapters.ai.types import ToolDefinition


class FakeModel:
    """Return scripted model turns and capture tool schemas."""

    def __init__(self, responses: Sequence[ChatResponse | Exception]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition] = (),
        tool_choice: str = "auto",
    ) -> ChatResponse:
        self.calls += 1
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.asyncio
async def test_toolbox_injects_tenant_and_hides_it_from_schema() -> None:
    """Model cannot select the tenant sent to an MCP tool."""

    class Session:
        async def list_tools(self) -> object:
            tool = SimpleNamespace(
                name="get_repro",
                description="Read a saved reproduction.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "installation_id": {"type": "integer"},
                        "run_id": {"type": "string"},
                    },
                    "required": ["installation_id", "run_id"],
                },
            )
            return SimpleNamespace(tools=[tool])

        async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
            return SimpleNamespace(
                structuredContent={"name": name, "arguments": arguments},
                isError=False,
            )

    toolbox = await ToolBox.from_mcp(Session(), frozenset({"get_repro"}), 42)
    assert toolbox.definitions[0].parameters["required"] == ["run_id"]
    result = await toolbox.call("get_repro", {"run_id": "run-1"})
    assert result == {
        "name": "get_repro",
        "arguments": {"run_id": "run-1", "installation_id": 42},
    }
    with pytest.raises(AiBadResponse, match="tenant identifier"):
        await toolbox.call("get_repro", {"run_id": "run-1", "installation_id": 99})


@pytest.mark.asyncio
async def test_tool_loop_stops_when_pr_head_is_stale() -> None:
    """Do not ask a model or call tools after the current head changes."""
    model = FakeModel([])
    toolbox = ToolBox((), {}, frozenset())

    async def stale() -> bool:
        return False

    result = await run_tool_loop(model, toolbox, (ChatMessage("user", "review"),), is_current=stale)
    assert result.stale
    assert model.calls == 0


@pytest.mark.asyncio
async def test_tool_loop_retries_provider_once_with_one_notice() -> None:
    """Transient outages retry with one caller-visible notice."""
    model = FakeModel(
        [AiUnavailable("offline"), ChatResponse("done", (), TokenUsage(3, 1), "test-model")]
    )
    notices: list[str] = []
    delays: list[float] = []
    usage: list[ChatResponse] = []

    async def notice(message: str) -> None:
        notices.append(message)

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async def record_usage(response: ChatResponse) -> None:
        usage.append(response)

    result = await run_tool_loop(
        model,
        ToolBox((), {}, frozenset()),
        (ChatMessage("user", "review"),),
        on_notice=notice,
        record_usage=record_usage,
        sleep=sleep,
    )
    assert result.text == "done"
    assert model.calls == 2
    assert len(notices) == 1
    assert delays == [0.5]
    assert usage[0].usage.input_tokens == 3


@pytest.mark.asyncio
async def test_tool_loop_does_not_retry_bad_responses() -> None:
    """Malformed provider responses fail without retrying as outages."""
    model = FakeModel(
        [AiBadResponse("bad payload"), ChatResponse("unused", (), TokenUsage(0, 0), "m")]
    )
    with pytest.raises(AiBadResponse):
        await run_tool_loop(model, ToolBox((), {}, frozenset()), (ChatMessage("user", "go"),))
    assert model.calls == 1
