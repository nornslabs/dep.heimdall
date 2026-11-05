"""Output guardrails in the runtime: the secret scan fails the run, the tone filter cleans prose."""

from collections.abc import Callable, Mapping
from typing import Self

from pydantic import BaseModel
from structlog.testing import capture_logs

from agents._runtime import (
    AgentBudget,
    AgentRun,
    AgentTool,
    Completed,
    CouldNotFinish,
    SubmitTool,
    ToolBox,
    run_agent,
)
from heimdall.adapters.ai import ChatMessage, ToolCall, ToolSpec
from tests.unit.agents.scripted_ai import ScriptedAi, tool_reply

REPRO_SPEC = ToolSpec(
    "start_repro",
    "Run the tests.",
    {"type": "object", "properties": {"test_cmd": {"type": "string"}}, "required": ["test_cmd"]},
)
BUDGET = AgentBudget(context_tokens=10_000, run_tokens=100_000, max_turns=4)
START = [ChatMessage("user", "review")]
FAKE_GITHUB_TOKEN = "gh" + "p_" + "A1b2C3d4" * 5


class Finding(BaseModel):
    file: str
    line: int
    text: str


class Review(BaseModel):
    """A payload that opts in to the tone filter, the way agent payloads will."""

    findings: tuple[Finding, ...]

    def filtered(self, clean: Callable[[str], str]) -> Self:
        cleaned = [f.model_copy(update={"text": clean(f.text)}) for f in self.findings]
        return self.model_copy(update={"findings": tuple(f for f in cleaned if f.text)})


class Verdict(BaseModel):
    """A payload without the tone filter."""

    summary: str


class ReproTool:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, arguments: Mapping[str, object]) -> object:
        self.calls += 1
        return "ok"


def _run[T: BaseModel](
    ai: ScriptedAi, submit: SubmitTool[T], repro: ReproTool | None = None
) -> AgentRun[T]:
    async def read_head() -> str:
        return "head-1"

    toolbox = ToolBox(frozenset({"start_repro"}), (AgentTool(REPRO_SPEC, repro or ReproTool()),))
    return AgentRun("huginn", ai, toolbox, submit, BUDGET, "head-1", read_head)


REVIEW = SubmitTool("submit_review", Review, "Finish with your findings.")
VERDICT = SubmitTool("submit_verdict", Verdict, "Finish with your verdict.")


def _finding(text: str, line: int = 3) -> dict[str, object]:
    return {"file": "src/a.py", "line": line, "text": text}


async def test_secret_in_submission_fails_the_run_and_logs_only_the_kind() -> None:
    summary = f"Tests pass with TOKEN={FAKE_GITHUB_TOKEN} set."
    ai = ScriptedAi([tool_reply(ToolCall("c1", "submit_verdict", {"summary": summary}))])

    with capture_logs() as logs:
        outcome = await run_agent(_run(ai, VERDICT), START)

    assert isinstance(outcome, CouldNotFinish)
    assert outcome.reason == "secret_in_output"
    blocked = [entry for entry in logs if entry["event"] == "agent_secret_blocked"]
    assert blocked == [
        {
            "event": "agent_secret_blocked",
            "log_level": "warning",
            "logger_name": "agents._turn",
            "agent": "huginn",
            "where": "submit_verdict",
            "kinds": ("github_token",),
        }
    ]
    assert FAKE_GITHUB_TOKEN not in repr(logs)


async def test_secret_in_tool_arguments_fails_the_run_before_the_tool_runs() -> None:
    repro = ReproTool()
    arguments = {"test_cmd": f"GITHUB_TOKEN={FAKE_GITHUB_TOKEN} pytest"}
    ai = ScriptedAi([tool_reply(ToolCall("c1", "start_repro", arguments))])

    outcome = await run_agent(_run(ai, VERDICT, repro), START)

    assert isinstance(outcome, CouldNotFinish)
    assert outcome.reason == "secret_in_output"
    assert repro.calls == 0


async def test_tone_filter_removes_a_sentence_about_the_author() -> None:
    text = "`load()` reads the file twice. The author clearly didn't test this."
    submit = ToolCall("c1", "submit_review", {"findings": [_finding(text)]})
    ai = ScriptedAi([tool_reply(submit)])

    outcome = await run_agent(_run(ai, REVIEW), START)

    assert isinstance(outcome, Completed)
    assert outcome.payload.findings == (
        Finding(file="src/a.py", line=3, text="`load()` reads the file twice."),
    )


async def test_finding_left_empty_by_the_tone_filter_is_dropped() -> None:
    findings = [_finding("Looks AI-generated.", 1), _finding("`b()` leaks a handle.", 2)]
    ai = ScriptedAi([tool_reply(ToolCall("c1", "submit_review", {"findings": findings}))])

    outcome = await run_agent(_run(ai, REVIEW), START)

    assert isinstance(outcome, Completed)
    assert [finding.line for finding in outcome.payload.findings] == [2]


async def test_payload_without_tone_filter_is_returned_unchanged() -> None:
    summary = "The author clearly didn't test this."
    ai = ScriptedAi([tool_reply(ToolCall("c1", "submit_verdict", {"summary": summary}))])

    outcome = await run_agent(_run(ai, VERDICT), START)

    assert outcome == Completed(Verdict(summary=summary), outcome.usage, 1)
