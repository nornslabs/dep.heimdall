"""A scripted model for Loki and Huginn, so the replay never calls a real provider."""

import json
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import cast

from heimdall.adapters.ai import ChatMessage, ChatResponse, ToolCall, ToolChoice, ToolSpec, Usage
from tests.e2e.fake_world import REPO_FULL_NAME

SCRIPTED_MODEL = "scripted-e2e"
NO_FINDINGS_REPLY = '{"findings": []}'
TEST_COMMAND = "pytest -q"
_RESULT = re.compile(r'^<pr_data source="([^"]+)">\n(.*)\n</pr_data>$', re.DOTALL)
_SCOPE = re.compile(r"installation_id (\d+) and pr_id ([0-9a-f-]{36})")


class ScriptedModel:
    """A ``ChatModel`` that always answers with one fixed reply, for Loki's single call."""

    def __init__(self, reply: str) -> None:
        """Keep the reply text the model returns; tests may replace ``reply`` between runs."""
        self.reply = reply
        self.calls: list[tuple[ChatMessage, ...]] = []

    @property
    def model(self) -> str:
        """The model name recorded in usage."""
        return SCRIPTED_MODEL

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        *,
        tool_choice: ToolChoice = "auto",
    ) -> ChatResponse:
        """Record the prompt and return the scripted reply."""
        self.calls.append(tuple(messages))
        return ChatResponse(self.reply, (), Usage(0, 0), SCRIPTED_MODEL)


class ScriptedHuginnModel:
    """Drive Huginn's runtime through one reproduction, reading its own tool results.

    ``before_reply`` lets the replay advance the pipeline between turns, so a started
    reproduction finishes before the model asks for it again.
    """

    def __init__(self, before_reply: Callable[[], Awaitable[None]] | None = None) -> None:
        """Bind the optional pipeline advance and start with no tool result yet."""
        self._before_reply = before_reply
        self._installation_id = 0
        self._pr_id = ""
        self._head_sha = ""
        self._run_id = ""
        self.calls: list[tuple[ChatMessage, ...]] = []

    @property
    def model(self) -> str:
        """The model name recorded in usage."""
        return SCRIPTED_MODEL

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        *,
        tool_choice: ToolChoice = "auto",
    ) -> ChatResponse:
        """Record the prompt, advance the pipeline and take the next reproduction step."""
        self.calls.append(tuple(messages))
        if self._before_reply is not None:
            await self._before_reply()
        if not self._pr_id:
            self._installation_id, self._pr_id = _tool_scope(messages)
        return self._reply(_last_result(messages))

    def _reply(self, result: tuple[str, dict[str, object]] | None) -> ChatResponse:
        """Choose the next call from the last tool result, as a model would."""
        if result is None:
            return _tool_reply(
                ToolCall("huginn-context", "get_pr_context", self._context_arguments())
            )
        name, values = result
        if name == "get_pr_context":
            self._head_sha = str(values["head_sha"])
            return _tool_reply(ToolCall("huginn-start", "start_repro", self._start_arguments()))
        if name == "start_repro":
            self._run_id = str(values["run_id"])
            return _tool_reply(ToolCall("huginn-poll", "get_repro", self._poll_arguments()))
        if name == "get_repro":
            if str(values.get("status")) != "finished":
                return _tool_reply(ToolCall("huginn-poll", "get_repro", self._poll_arguments()))
            verdict = self._verdict(values)
            return _tool_reply(ToolCall("huginn-submit", "submit_verdict", verdict))
        raise AssertionError(f"unexpected Huginn tool result: {name}")

    def _context_arguments(self) -> dict[str, object]:
        """Read the PR metadata for the scope the prompt named."""
        return {
            "installation_id": self._installation_id,
            "pr_id": self._pr_id,
            "repo_full_name": REPO_FULL_NAME,
        }

    def _start_arguments(self) -> dict[str, object]:
        """Start one paired run with the repository's configured command."""
        return {
            "installation_id": self._installation_id,
            "pr_id": self._pr_id,
            "head_sha": self._head_sha,
            "test_cmd": TEST_COMMAND,
        }

    def _poll_arguments(self) -> dict[str, object]:
        """Read the run this model started."""
        return {"installation_id": self._installation_id, "run_id": self._run_id}

    def _verdict(self, run: dict[str, object]) -> dict[str, object]:
        """Turn the saved run into the verdict a maintainer would expect from it."""
        with_fix = _counters(run.get("with_fix"))
        without_fix = _counters(run.get("without_fix"))
        if with_fix["errors"] or without_fix["errors"]:
            verdict = "setup_failed"
        elif with_fix["passed"] and without_fix["passed"]:
            verdict = "not_covered"
        elif with_fix["passed"]:
            verdict = "reproduced"
        else:
            verdict = "setup_failed"
        return {
            "verdict": verdict,
            "test_cmd": TEST_COMMAND,
            "repro_ids": [self._run_id],
            "with_fix": with_fix,
            "without_fix": without_fix,
            "failing_assertion": _first_failure(run),
            "summary": "Scripted reproduction for the e2e replay.",
        }


def _tool_reply(*calls: ToolCall) -> ChatResponse:
    """Build a model reply that asks for one or more tools."""
    return ChatResponse(None, calls, Usage(0, 0), SCRIPTED_MODEL)


def _tool_scope(messages: Sequence[ChatMessage]) -> tuple[int, str]:
    """Read the tenant and PR ids Huginn's prompt says its tools are scoped to."""
    for message in messages:
        match = _SCOPE.search(message.content)
        if match is not None:
            return int(match.group(1)), match.group(2)
    raise AssertionError("Huginn's prompt is missing its tool scope")


def _last_result(messages: Sequence[ChatMessage]) -> tuple[str, dict[str, object]] | None:
    """Read the last tool result and the tool that produced it, or None before any call."""
    for message in reversed(messages):
        if message.role != "tool":
            continue
        match = _RESULT.match(message.content)
        if match is None:
            raise AssertionError(
                f"Huginn tool result is not wrapped as pr_data: {message.content[:200]!r}"
            )
        return match.group(1), json.loads(match.group(2))
    return None


def _counters(value: object) -> dict[str, int]:
    """Count one saved mode as one run, the way a verdict counts it."""
    passed = isinstance(value, dict) and cast(dict[str, object], value).get("passed") is True
    return {"passed": 1 if passed else 0, "failed": 0 if passed else 1, "errors": 0}


def _first_failure(run: dict[str, object]) -> str:
    """Quote the first saved failure, preferring the run without the fix."""
    for mode in ("without_fix", "with_fix"):
        value = run.get(mode)
        if isinstance(value, dict):
            failures = cast(dict[str, object], value).get("failures")
            if isinstance(failures, list) and failures:
                return str(cast(list[object], failures)[0])
    return ""
