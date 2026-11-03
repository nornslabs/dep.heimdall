"""Huginn reproduction polling and verdict messaging."""

import runpy
import uuid
from pathlib import Path

from heimdall.modules.repro.api import ReproStatus, Verdict

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
HUGINN = runpy.run_path(str(REPOSITORY_ROOT / "agents/huginn/agent.py"))
MAX_TURNS = HUGINN["MAX_TURNS"]
run_huginn = HUGINN["run_huginn"]


class FakeHuginnTools:
    """Return a configured sequence of run records."""

    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.run_id = uuid.uuid4()
        self.poll_count = 0
        self.start_arguments: tuple[int, str, str, str] | None = None

    async def start_repro(
        self, installation_id: int, pr_id: str, head_sha: str, test_cmd: str
    ) -> dict[str, str | int]:
        """Record inputs and return the attempt identity."""
        self.start_arguments = (installation_id, pr_id, head_sha, test_cmd)
        return {"run_id": str(self.run_id), "attempt": 1, "head_sha": head_sha, "status": "queued"}

    async def get_repro(self, installation_id: int, run_id: str) -> dict[str, object]:
        """Return the next status response."""
        self.poll_count += 1
        index = min(self.poll_count - 1, len(self.responses) - 1)
        return self.responses[index]


async def test_run_huginn_reports_saved_paired_verdict() -> None:
    """Stop polling once the saved attempt is terminal and summarize its verdict."""
    tools = FakeHuginnTools(
        [
            {
                "run_id": "00000000-0000-0000-0000-000000000001",
                "pr_id": "00000000-0000-0000-0000-000000000002",
                "head_sha": "head-1",
                "attempt": 1,
                "status": "finished",
                "with_fix": {"passed": True},
                "without_fix": {"passed": False},
                "verdict": "fix_confirmed",
            }
        ]
    )
    waits: list[float] = []

    async def no_wait(delay: float) -> None:
        waits.append(delay)

    result = await run_huginn(
        tools,
        123,
        uuid.UUID("00000000-0000-0000-0000-000000000002"),
        "head-1",
        "pytest -q",
        sleep=no_wait,
    )

    assert result.status is ReproStatus.FINISHED
    assert result.verdict is Verdict.FIX_CONFIRMED
    assert "passed with the fix and failed without" in result.message
    assert tools.start_arguments == (
        123,
        "00000000-0000-0000-0000-000000000002",
        "head-1",
        "pytest -q",
    )
    assert waits == []


async def test_run_huginn_stops_after_turn_limit() -> None:
    """Bound repeated pending polls and return the last persisted status."""
    run_id = uuid.uuid4()
    pr_id = uuid.uuid4()
    pending = {
        "run_id": str(run_id),
        "pr_id": str(pr_id),
        "head_sha": "head-1",
        "attempt": 1,
        "status": "running",
        "with_fix": None,
        "without_fix": None,
        "verdict": None,
    }
    tools = FakeHuginnTools([pending])

    async def no_wait(_delay: float) -> None:
        return None

    result = await run_huginn(tools, 123, pr_id, "head-1", "pytest -q", sleep=no_wait)

    assert result.status is ReproStatus.RUNNING
    assert result.verdict is None
    assert tools.poll_count == MAX_TURNS
    assert "still running" in result.message
