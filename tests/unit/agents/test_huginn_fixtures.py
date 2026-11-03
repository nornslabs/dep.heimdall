"""Replay saved PR and reproduction-result fixtures through Huginn's loop."""

import asyncio
import json
import runpy
import uuid
from pathlib import Path
from typing import cast

import pytest

from heimdall.modules.repro.api import MAX_REPRO_RUNS, ReproStatus, Verdict

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = REPOSITORY_ROOT / "tests/fixtures/huginn"
HUGINN = runpy.run_path(str(REPOSITORY_ROOT / "agents/huginn/agent.py"))
MAX_POLLS: int = HUGINN["MAX_POLLS"]
run_huginn = HUGINN["run_huginn"]


class RecordedHuginnTools:
    """Replay the one persisted tool result captured for a PR fixture."""

    def __init__(self, run: dict[str, object]) -> None:
        """Keep a saved result and count calls to the two allowed tools."""
        self._run = run
        self.start_count = 0
        self.result_count = 0
        self._run_ids: list[uuid.UUID] = []
        self.start_arguments: list[tuple[int, str, str, str]] = []

    async def start_repro(
        self, installation_id: int, pr_id: str, head_sha: str, test_cmd: str
    ) -> dict[str, str | int]:
        """Return the fixture's run id as if the scoped MCP tool queued it."""
        self.start_count += 1
        self.start_arguments.append((installation_id, pr_id, head_sha, test_cmd))
        run_id = uuid.UUID(int=self.start_count)
        self._run_ids.append(run_id)
        return {
            "run_id": str(run_id),
            "attempt": self.start_count,
            "head_sha": head_sha,
            "status": "queued",
        }

    async def get_repro(self, installation_id: int, run_id: str) -> dict[str, object]:
        """Return the single persisted result recorded in the fixture."""
        self.result_count += 1
        result = self._run.copy()
        result["run_id"] = run_id
        result["attempt"] = self._run_ids.index(uuid.UUID(run_id)) + 1
        return result


async def _yield_once(_delay: float) -> None:
    await asyncio.sleep(0)


@pytest.mark.parametrize(
    "fixture_path",
    sorted(FIXTURE_DIR.glob("*.json")),
    ids=lambda fixture_path: fixture_path.stem,
)
async def test_huginn_replays_recorded_pr_fixtures(fixture_path: Path) -> None:
    """Parse saved outcomes, stay within caps, and ignore instructions in PR text."""
    fixture = cast(dict[str, object], json.loads(fixture_path.read_text()))
    run = cast(dict[str, object], fixture["run"])
    tools = RecordedHuginnTools(run)
    pr_id = uuid.UUID(str(run["pr_id"]))
    configured_command = str(fixture["configured_test_cmd"])

    result = await run_huginn(
        tools,
        123,
        pr_id,
        str(run["head_sha"]),
        configured_command,
        sleep=_yield_once,
    )

    expected_verdict = fixture["expected_verdict"]
    expected = Verdict(str(expected_verdict)) if expected_verdict is not None else None
    assert result.status == ReproStatus(str(run["status"]))
    assert result.verdict == expected
    assert f"Paired attempts: {3 if expected is not None else 0}/3 completed." in result.message
    assert tools.start_count == MAX_REPRO_RUNS
    assert tools.result_count == MAX_REPRO_RUNS <= MAX_REPRO_RUNS * MAX_POLLS
    assert tools.start_arguments
    assert all(
        arguments == (123, str(pr_id), str(run["head_sha"]), configured_command)
        for arguments in tools.start_arguments
    )
    assert configured_command == "pytest -q"
    assert str(fixture["pr_body"]) not in " ".join(
        str(argument) for arguments in tools.start_arguments for argument in arguments
    )
    expected_failure = fixture.get("expected_failure")
    if expected_failure is not None:
        assert str(expected_failure) in result.message
