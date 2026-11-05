"""Huginn's run limit and runner rule, exercised through the runtime and its guard."""

from collections.abc import Awaitable, Callable, Mapping, Sequence

from agents._outcome import Completed, RunOutcome
from agents._runtime import AgentBudget, ToolBox
from agents._toolbox import AgentTool
from agents.huginn.agent import run_huginn
from agents.huginn.guardrails import ReproGuard, guard_start_repro
from agents.huginn.schemas import HuginnVerdict, submit_verdict_tool
from heimdall.adapters.ai import ChatMessage, ChatResponse, ToolCall, ToolSpec
from heimdall.modules.repro.api import MAX_REPRO_RUNS
from tests.unit.agents.scripted_ai import ScriptedAi, tool_reply

INSTALLATION_ID = 42
HEAD_SHA = "head-1"
RUN_ID = "00000000-0000-0000-0000-000000000001"
NO_CAPS = AgentBudget(context_tokens=24_000, run_tokens=150_000, max_turns=12)


class _Runs:
    """Record every accepted start and report one fixed finished run."""

    def __init__(self) -> None:
        self.starts: list[dict[str, object]] = []

    async def start_repro(self, arguments: Mapping[str, object]) -> dict[str, object]:
        """Record an accepted run and return its identity."""
        self.starts.append(dict(arguments))
        return {"run_id": RUN_ID, "attempt": 1, "head_sha": arguments.get("head_sha")}

    async def get_repro(self, installation_id: int, run_id: str) -> dict[str, object]:
        """Return the saved run the verdict cites."""
        assert (installation_id, run_id) == (INSTALLATION_ID, RUN_ID)
        return {
            "head_sha": HEAD_SHA,
            "with_fix": {"passed": True, "failures": []},
            "without_fix": {"passed": False, "failures": ["boom"]},
        }


def _start(test_cmd: str) -> dict[str, object]:
    """Build one ``start_repro`` argument set."""
    return {
        "installation_id": INSTALLATION_ID,
        "pr_id": "00000000-0000-0000-0000-000000000002",
        "head_sha": HEAD_SHA,
        "test_cmd": test_cmd,
    }


def _submit() -> dict[str, object]:
    """Build a verdict that matches the one run ``_Runs`` reports."""
    return {
        "verdict": "reproduced",
        "test_cmd": "pytest -q",
        "repro_ids": [RUN_ID],
        "with_fix": {"passed": 1, "failed": 0, "errors": 0},
        "without_fix": {"passed": 0, "failed": 1, "errors": 0},
        "failing_assertion": "boom",
        "summary": "The paired run reproduces the issue.",
    }


def _head_reader() -> Callable[[], Awaitable[str]]:
    """Return a head check that never moves."""

    async def read() -> str:
        return HEAD_SHA

    return read


async def _outcome(
    allowed_runners: tuple[str, ...], commands: Sequence[str], runs: _Runs
) -> RunOutcome[HuginnVerdict]:
    """Run Huginn with a guarded ``start_repro`` and one scripted command per turn."""
    start = AgentTool(ToolSpec("start_repro", "Queue a run.", {"type": "object"}), runs.start_repro)
    toolbox = ToolBox(
        frozenset({"start_repro"}),
        (guard_start_repro(ReproGuard(allowed_runners), start),),
        retry_delays=(),
    )
    replies: list[ChatResponse] = [
        tool_reply(ToolCall(f"start-{index}", "start_repro", _start(command)))
        for index, command in enumerate(commands)
    ]
    replies.append(tool_reply(ToolCall("done", "submit_verdict", _submit())))
    return await run_huginn(
        ai=ScriptedAi(replies),
        toolbox=toolbox,
        submit=submit_verdict_tool(runs, INSTALLATION_ID, HEAD_SHA),
        head_sha=HEAD_SHA,
        read_current_head=_head_reader(),
        messages=(ChatMessage("user", "Reproduce this pull request."),),
        budget=NO_CAPS,
    )


async def test_a_fourth_start_repro_is_refused() -> None:
    """Four asks produce three runs; the fourth comes back to the model as an error."""
    runs = _Runs()

    outcome = await _outcome(("pytest",), ["pytest -q"] * (MAX_REPRO_RUNS + 1), runs)

    assert isinstance(outcome, Completed)
    assert len(runs.starts) == MAX_REPRO_RUNS


async def test_a_runner_change_between_runs_is_refused() -> None:
    """Narrowing the command is fine; swapping pytest for npm test is not."""
    runs = _Runs()

    outcome = await _outcome(("pytest", "npm test"), ["pytest -q", "npm test"], runs)

    assert isinstance(outcome, Completed)
    assert [start["test_cmd"] for start in runs.starts] == ["pytest -q"]
