"""Scripted models for Loki and Huginn, so the replay never calls a real provider."""

import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import cast

from heimdall.adapters.ai import ChatMessage, ChatResponse, ToolCall, ToolChoice, ToolSpec, Usage
from tests.e2e.fake_world import REPO_FULL_NAME

SCRIPTED_MODEL = "scripted-e2e"
TEST_COMMAND = "pytest -q"
REVIEW_SUMMARY = "Scripted review for the e2e replay."
_RESULT = re.compile(r'^<pr_data source="([^"]+)">\n(.*)\n</pr_data>$', re.DOTALL)
_SCOPE = re.compile(r"installation_id (\d+) and pr_id ([0-9a-f-]{36})")


class ScriptedLokiModel:
    """Drive Loki's runtime: read the PR and its diff, save the scripted findings, submit.

    The replay sets the findings before each review; an empty tuple means a clean pass.
    """

    def __init__(self) -> None:
        """Start with no findings and no PR scope yet."""
        self._findings: tuple[dict[str, object], ...] = ()
        self._installation_id = 0
        self._pr_id = ""
        self._head_sha = ""
        self._pr_number = 0
        self.calls: list[tuple[ChatMessage, ...]] = []

    def set_findings(self, findings: Sequence[Mapping[str, object]]) -> None:
        """Use these findings for the next review; empty means a clean pass."""
        self._findings = tuple(dict(finding) for finding in findings)

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
        """Record the prompt and take the next review step from the last tool result."""
        self.calls.append(tuple(messages))
        if not self._pr_id:
            self._installation_id, self._pr_id = _tool_scope(messages)
        return self._reply(_last_result(messages))

    def _reply(self, result: tuple[str, dict[str, object]] | None) -> ChatResponse:
        """Choose the next call from the last tool result, as a model would."""
        if result is None:
            return _tool_reply(
                ToolCall("loki-context", "get_pr_context", self._context_arguments())
            )
        name, values = result
        if name == "get_pr_context":
            self._pr_number = int(str(values["number"]))
            self._head_sha = str(values["head_sha"])
            if not self._findings:
                return _tool_reply(ToolCall("loki-submit", "submit_review", self._clean_pass()))
            return _tool_reply(ToolCall("loki-diff", "get_diff", self._diff_arguments()))
        if name == "get_diff":
            return _tool_reply(ToolCall("loki-save", "save_findings", self._save_arguments()))
        if name == "save_findings":
            return _tool_reply(ToolCall("loki-submit", "submit_review", self._submitted(values)))
        raise AssertionError(f"unexpected Loki tool result: {name}")

    def _context_arguments(self) -> dict[str, object]:
        """Read the PR metadata for the scope the prompt named."""
        return {
            "installation_id": self._installation_id,
            "pr_id": self._pr_id,
            "repo_full_name": REPO_FULL_NAME,
        }

    def _diff_arguments(self) -> dict[str, object]:
        """Read the diff of the PR the scripted model just looked up."""
        return {
            "installation_id": self._installation_id,
            "repo_full_name": REPO_FULL_NAME,
            "pr_number": self._pr_number,
        }

    def _save_arguments(self) -> dict[str, object]:
        """Save the scripted findings against the head the scripted model observed."""
        return {
            "installation_id": self._installation_id,
            "pr_id": self._pr_id,
            "repo_full_name": REPO_FULL_NAME,
            "head_sha": self._head_sha,
            "findings": [dict(finding) for finding in self._findings],
        }

    def _clean_pass(self) -> dict[str, object]:
        """Submit a review with nothing to report."""
        return {"skipped_reason": "no_findings", "summary": REVIEW_SUMMARY}

    def _submitted(self, values: dict[str, object]) -> dict[str, object]:
        """Submit the ids save_findings returned, or a clean pass when it accepted none."""
        ids = values.get("finding_ids")
        finding_ids = (
            [str(value) for value in cast(list[object], ids)] if isinstance(ids, list) else []
        )
        if not finding_ids:
            return self._clean_pass()
        return {"finding_ids": finding_ids, "summary": REVIEW_SUMMARY}


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


class ScriptedHeimdallModel:
    """Drive Heimdall's runtime: save the review card from the brief, then submit_result."""

    def __init__(self) -> None:
        """Start with no calls recorded."""
        self.calls: list[tuple[ChatMessage, ...]] = []
        self._installation_id = 0
        self._pr_id = ""

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
        """Record the prompt, save the card, and finish by submitting the card id."""
        self.calls.append(tuple(messages))
        if not self._pr_id:
            self._installation_id, self._pr_id = _tool_scope(messages)
        result = _last_result(messages)
        if result is None:
            brief = _brief_payload(messages)
            pr = cast(dict[str, object], brief.get("pull_request", {}))
            repro_status = str(brief.get("repro_status", "No reproduction run is available."))
            findings = cast(list[str], brief.get("findings", []))
            save_args: dict[str, object] = {
                "installation_id": self._installation_id,
                "pr_id": self._pr_id,
                "number": int(str(pr.get("number", 0))),
                "title": str(pr.get("title", "")),
                "head_sha": str(pr.get("head_sha", "")),
                "rules_summary": "All intake rules passed.",
                "fix_status": repro_status,
                "gaps": findings,
                "suggested_action": "Review the card, then approve the comment to post it.",
            }
            return _tool_reply(ToolCall("heimdall-card", "save_card", save_args))
        name, values = result
        if name == "save_card":
            card_id = str(values["card_id"])
            return _tool_reply(
                ToolCall(
                    "heimdall-submit",
                    "submit_result",
                    {"action": "card", "card_id": card_id},
                )
            )
        raise AssertionError(f"unexpected Heimdall tool result: {name}")


_BRIEF_PATTERN = re.compile(r'<pr_data source="pr_brief">\n(.*?)\n</pr_data>', re.DOTALL)


def _brief_payload(messages: Sequence[ChatMessage]) -> dict[str, object]:
    """Extract and parse the pr_brief payload from prompt messages."""
    for message in messages:
        match = _BRIEF_PATTERN.search(message.content)
        if match is not None:
            return cast(dict[str, object], json.loads(match.group(1)))
    return {}


def _tool_reply(*calls: ToolCall) -> ChatResponse:
    """Build a model reply that asks for one or more tools."""
    return ChatResponse(None, calls, Usage(0, 0), SCRIPTED_MODEL)


def _tool_scope(messages: Sequence[ChatMessage]) -> tuple[int, str]:
    """Read the tenant and PR ids an agent's prompt says its tools are scoped to."""
    for message in messages:
        match = _SCOPE.search(message.content)
        if match is not None:
            return int(match.group(1)), match.group(2)
    raise AssertionError("agent's prompt is missing its tool scope")


def _last_result(messages: Sequence[ChatMessage]) -> tuple[str, dict[str, object]] | None:
    """Read the last tool result and the tool that produced it, or None before any call."""
    for message in reversed(messages):
        if message.role != "tool":
            continue
        match = _RESULT.match(message.content)
        if match is None:
            raise AssertionError(
                f"agent tool result is not wrapped as pr_data: {message.content[:200]!r}"
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
