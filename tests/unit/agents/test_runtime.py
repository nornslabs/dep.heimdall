"""Agent runtime plumbing: allowlisted ToolBox, bounded tool loop, usage and stale drops."""

from collections.abc import Mapping

import pytest
from pydantic import SecretStr
from structlog.testing import capture_logs

from agents._lifecycle import warn_if_shared_model
from agents._runtime import (
    AgentTool,
    AgentTurnLimitError,
    RunUsage,
    ToolBox,
    run_tool_loop,
    still_current,
)
from heimdall.adapters.ai import AiConfig, ChatMessage, ToolCall, ToolSpec, Usage
from heimdall.shared.errors import ToolNotAllowed
from tests.unit.agents.scripted_ai import ScriptedAi, text_reply, tool_reply

DIFF_SPEC = ToolSpec("get_diff", "Read the diff.", {"type": "object"})


async def _diff(arguments: Mapping[str, object]) -> object:
    return {"text": f"diff for {arguments.get('pr')}"}


async def _broken(arguments: Mapping[str, object]) -> object:
    raise RuntimeError("secret PR text must not leak")


def _toolbox(*tools: AgentTool) -> ToolBox:
    return ToolBox(frozenset({"get_diff", "broken"}), tools or (AgentTool(DIFF_SPEC, _diff),))


async def test_tool_loop_runs_tool_calls_and_stops_on_text_reply() -> None:
    ai = ScriptedAi([tool_reply(ToolCall("c1", "get_diff", {"pr": 7})), text_reply("done")])

    result = await run_tool_loop(ai, _toolbox(), [ChatMessage("user", "review")], max_turns=3)

    assert result.reply.text == "done"
    second_request = ai.requests[1][0]
    assert second_request[1] == ChatMessage(
        "assistant", "", tool_calls=(ToolCall("c1", "get_diff", {"pr": 7}),)
    )
    assert second_request[2] == ChatMessage("tool", '{"text": "diff for 7"}', tool_call_id="c1")
    assert ai.requests[0][1] == (DIFF_SPEC,)


async def test_unknown_tool_is_refused_and_reported_to_the_model() -> None:
    ai = ScriptedAi([tool_reply(ToolCall("c1", "start_repro", {})), text_reply("ok")])

    await run_tool_loop(ai, _toolbox(), [ChatMessage("user", "go")], max_turns=3)

    tool_message = ai.requests[1][0][-1]
    assert tool_message.role == "tool"
    assert tool_message.content == "error: tool 'start_repro' is not allowed"


async def test_tool_error_goes_back_as_text_without_its_message() -> None:
    toolbox = _toolbox(AgentTool(ToolSpec("broken", "Fails.", {}), _broken))
    ai = ScriptedAi([tool_reply(ToolCall("c1", "broken", {})), text_reply("ok")])

    await run_tool_loop(ai, toolbox, [ChatMessage("user", "go")], max_turns=3)

    tool_message = ai.requests[1][0][-1]
    assert tool_message.content == "error: RuntimeError"


async def test_tool_loop_stops_at_max_turns_with_the_usage_spent() -> None:
    ai = ScriptedAi([tool_reply(ToolCall(f"c{i}", "get_diff", {})) for i in range(5)])

    with pytest.raises(AgentTurnLimitError) as raised:
        await run_tool_loop(ai, _toolbox(), [ChatMessage("user", "go")], max_turns=2)

    assert len(ai.requests) == 2
    assert raised.value.usage.usage == Usage(10, 2)


async def test_run_usage_sums_tokens_and_keeps_the_answering_model() -> None:
    ai = ScriptedAi(
        [
            tool_reply(ToolCall("c1", "get_diff", {}), model="provider/model-a"),
            text_reply("done", model="provider/model-a", usage=Usage(20, 4)),
        ]
    )

    result = await run_tool_loop(ai, _toolbox(), [ChatMessage("user", "go")], max_turns=3)

    assert result.usage == RunUsage(Usage(25, 5), "provider/model-a")
    record = result.usage.to_record("loki", None)
    assert (record.model, record.input_tokens, record.output_tokens) == ("provider/model-a", 25, 5)


def test_run_usage_without_calls_falls_back_to_configured_model() -> None:
    record = RunUsage().to_record("loki", None, fallback_model="configured")

    assert (record.model, record.input_tokens) == ("configured", 0)


def test_toolbox_rejects_registering_a_tool_outside_the_allowlist() -> None:
    with pytest.raises(ToolNotAllowed, match="save_card"):
        ToolBox(frozenset({"get_diff"}), (AgentTool(ToolSpec("save_card", "", {}), _diff),))


async def test_result_is_dropped_when_the_head_changed() -> None:
    async def moved_head() -> str:
        return "head-2"

    with capture_logs() as logs:
        current = await still_current("head-1", moved_head, agent="loki")

    assert current is False
    assert logs[0]["event"] == "agent_result_dropped_head_changed"


async def test_result_is_kept_when_the_head_is_unchanged() -> None:
    async def same_head() -> str:
        return "head-1"

    assert await still_current("head-1", same_head, agent="loki") is True


def _config(model: str) -> AiConfig:
    return AiConfig(base_url="http://x", api_key=SecretStr("k"), model=model)


def test_shared_model_with_loki_logs_a_warning() -> None:
    with capture_logs() as logs:
        shared = warn_if_shared_model(_config("m-1"), {"huginn": _config("m-1")})

    assert shared is True
    assert logs[0]["event"] == "loki_same_model_as_other_agents"
    assert logs[0]["agents"] == ["huginn"]


def test_distinct_models_log_nothing() -> None:
    with capture_logs() as logs:
        shared = warn_if_shared_model(_config("m-1"), {"huginn": _config("m-2")})

    assert shared is False
    assert logs == []
