"""Huginn attempt orchestration and verdict reporting."""

import runpy
import uuid
from pathlib import Path

import pytest

from heimdall.modules.repro.api import MAX_REPRO_RUNS, ReproStatus, Verdict

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
HUGINN = runpy.run_path(str(REPOSITORY_ROOT / "agents/huginn/agent.py"))
MAX_POLLS = HUGINN["MAX_POLLS"]
run_huginn = HUGINN["run_huginn"]


class FakeHuginnTools:
    """Return per-attempt records and capture trusted start arguments."""

    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.run_ids: list[uuid.UUID] = []
        self.polls_by_run: dict[str, int] = {}
        self.start_count = 0
        self.start_arguments: list[tuple[int, str, str, str]] = []

    async def start_repro(
        self, installation_id: int, pr_id: str, head_sha: str, test_cmd: str
    ) -> dict[str, str | int]:
        """Record inputs and return a unique attempt identity."""
        self.start_count += 1
        self.start_arguments.append((installation_id, pr_id, head_sha, test_cmd))
        run_id = uuid.UUID(int=self.start_count)
        self.run_ids.append(run_id)
        return {"run_id": str(run_id), "attempt": self.start_count, "head_sha": head_sha}

    async def get_repro(self, installation_id: int, run_id: str) -> dict[str, object]:
        """Return that run's next saved response."""
        self.polls_by_run[run_id] = self.polls_by_run.get(run_id, 0) + 1
        run_index = self.run_ids.index(uuid.UUID(run_id))
        response = self.responses[min(run_index, len(self.responses) - 1)].copy()
        response["run_id"] = run_id
        response.setdefault("attempt", run_index + 1)
        response.setdefault("pr_id", "00000000-0000-0000-0000-000000000002")
        return response


@pytest.mark.asyncio
async def test_huginn_runs_three_attempts_and_reports_counts_and_assertions() -> None:
    """Aggregate saved paired results into one majority verdict message."""
    response: dict[str, object] = {
        "pr_id": "00000000-0000-0000-0000-000000000002",
        "head_sha": "head-1",
        "status": "finished",
        "with_fix": {"passed": True, "failures": []},
        "without_fix": {
            "passed": False,
            "failures": ["tests/test_bug.py::test_regression: assert result == expected"],
        },
        "verdict": "fix_confirmed",
    }
    tools = FakeHuginnTools([response])
    waits: list[float] = []

    async def no_wait(delay: float) -> None:
        waits.append(delay)

    pr_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    result = await run_huginn(tools, 123, pr_id, "head-1", "pytest -q", sleep=no_wait)

    assert result.status is ReproStatus.FINISHED
    assert result.verdict is Verdict.FIX_CONFIRMED
    assert len(result.run_ids) == MAX_REPRO_RUNS
    assert "Passed with fix: 3/3" in result.message
    assert "passed without fix: 0/3" in result.message
    assert "assert result == expected" in result.message
    assert tools.start_count == MAX_REPRO_RUNS
    assert all(args == (123, str(pr_id), "head-1", "pytest -q") for args in tools.start_arguments)
    assert waits == []


@pytest.mark.asyncio
async def test_huginn_withholds_verdict_when_attempts_disagree() -> None:
    """A tie across persisted run verdicts is reported as mixed results."""
    base: dict[str, object] = {
        "pr_id": "00000000-0000-0000-0000-000000000002",
        "head_sha": "head-1",
        "status": "finished",
        "with_fix": {"passed": True},
        "without_fix": {"passed": False},
    }
    results: list[dict[str, object]] = [
        {**base, "verdict": "fix_confirmed"},
        {**base, "verdict": "fix_failed"},
        {**base, "verdict": "not_reproduced"},
    ]
    result = await run_huginn(
        FakeHuginnTools(results),
        123,
        uuid.UUID("00000000-0000-0000-0000-000000000002"),
        "head-1",
        "pytest -q",
        sleep=_no_wait,
    )
    assert result.verdict is None
    assert "mixed results" in result.message


@pytest.mark.asyncio
async def test_huginn_stops_after_bounded_polling() -> None:
    """Return still-running when saved attempts never reach a terminal state."""
    pending: dict[str, object] = {
        "pr_id": "00000000-0000-0000-0000-000000000002",
        "head_sha": "head-1",
        "status": "running",
        "with_fix": None,
        "without_fix": None,
        "verdict": None,
    }
    tools = FakeHuginnTools([pending])
    result = await run_huginn(
        tools,
        123,
        uuid.UUID("00000000-0000-0000-0000-000000000002"),
        "head-1",
        "pytest -q",
        sleep=_no_wait,
    )
    assert result.status is ReproStatus.RUNNING
    assert result.verdict is None
    assert tools.start_count == MAX_REPRO_RUNS
    assert all(count == MAX_POLLS for count in tools.polls_by_run.values())
    assert "still running" in result.message


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt", ["1", True, 1.0])
async def test_huginn_rejects_a_run_record_whose_attempt_is_not_an_integer(
    attempt: object,
) -> None:
    """Treat a malformed MCP attempt number as an invalid record, not a coerced value."""
    malformed: dict[str, object] = {
        "pr_id": "00000000-0000-0000-0000-000000000002",
        "head_sha": "head-1",
        "attempt": attempt,
        "status": "running",
        "with_fix": None,
        "without_fix": None,
        "verdict": None,
    }
    tools = FakeHuginnTools([malformed])
    pr_id = uuid.UUID("00000000-0000-0000-0000-000000000002")

    with pytest.raises(ValueError, match="invalid run record"):
        await run_huginn(tools, 123, pr_id, "head-1", "pytest -q", sleep=_no_wait)


async def _no_wait(_delay: float) -> None:
    """Skip wall-clock delays in bounded unit tests."""
