"""ToolBox guards: allow-list, JSON-schema argument checks and outage retries."""

from collections.abc import Mapping

import pytest

from agents._toolbox import AgentTool, ToolBox
from heimdall.adapters.ai import ToolSpec
from heimdall.shared.errors import (
    GitHubUnavailable,
    ReproLimitReached,
    ToolNotAllowed,
    ToolUnavailable,
)

FILE_SPEC = ToolSpec(
    "get_file",
    "Read part of a file.",
    {
        "type": "object",
        "properties": {"path": {"type": "string"}, "start": {"type": "integer"}},
        "required": ["path"],
        "additionalProperties": False,
    },
)


class Recorder:
    """A tool handler that records each call and fails as told."""

    def __init__(self, *failures: Exception) -> None:
        self.calls: list[Mapping[str, object]] = []
        self._failures = list(failures)

    async def __call__(self, arguments: Mapping[str, object]) -> object:
        self.calls.append(arguments)
        if self._failures:
            raise self._failures.pop(0)
        return {"text": "line 1"}


async def _no_sleep(_seconds: float) -> None:
    return None


def _toolbox(handler: Recorder) -> ToolBox:
    return ToolBox(frozenset({"get_file"}), (AgentTool(FILE_SPEC, handler),), sleep=_no_sleep)


async def test_allowed_tool_with_valid_arguments_runs() -> None:
    handler = Recorder()

    result = await _toolbox(handler).call("get_file", {"path": "a.py", "start": 3})

    assert (result.text, result.ran, result.failed) == ('{"text": "line 1"}', True, False)
    assert handler.calls == [{"path": "a.py", "start": 3}]


async def test_unknown_tool_is_refused_and_handler_never_runs() -> None:
    handler = Recorder()

    result = await _toolbox(handler).call("start_repro", {})

    assert (result.text, result.ran) == ("error: tool 'start_repro' is not allowed", False)
    assert handler.calls == []


async def test_bad_argument_is_refused_before_the_tool_runs() -> None:
    handler = Recorder()

    result = await _toolbox(handler).call("get_file", {"path": 7, "extra": True})

    assert result.ran is False
    assert result.text.startswith("error: invalid arguments for 'get_file':")
    assert "$.path" in result.text
    assert handler.calls == []


async def test_missing_required_argument_is_refused() -> None:
    result = await _toolbox(Recorder()).call("get_file", {})

    assert "'path' is a required property" in result.text


async def test_non_retryable_tool_error_goes_back_to_the_model_without_its_message() -> None:
    handler = Recorder(ReproLimitReached("secret PR text"))

    result = await _toolbox(handler).call("get_file", {"path": "a.py"})

    assert (result.text, result.ran, result.failed) == ("error: ReproLimitReached", True, True)
    assert len(handler.calls) == 1


async def test_retryable_tool_error_is_retried_until_it_succeeds() -> None:
    handler = Recorder(GitHubUnavailable("down"), GitHubUnavailable("down"))

    result = await _toolbox(handler).call("get_file", {"path": "a.py"})

    assert result.text == '{"text": "line 1"}'
    assert len(handler.calls) == 3


async def test_retryable_tool_error_after_three_attempts_is_unavailable() -> None:
    handler = Recorder(*(GitHubUnavailable("down") for _ in range(3)))

    with pytest.raises(ToolUnavailable, match="get_file"):
        await _toolbox(handler).call("get_file", {"path": "a.py"})

    assert len(handler.calls) == 3


def test_toolbox_rejects_registering_a_tool_outside_the_allowlist() -> None:
    with pytest.raises(ToolNotAllowed, match="get_file"):
        ToolBox(frozenset({"get_diff"}), (AgentTool(FILE_SPEC, Recorder()),))


def test_toolbox_lists_the_specs_it_offers() -> None:
    assert _toolbox(Recorder()).specs() == (FILE_SPEC,)
