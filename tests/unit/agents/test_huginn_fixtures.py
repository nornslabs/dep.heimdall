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
MAX_TURNS: int = HUGINN["MAX_TURNS"]
run_huginn = HUGINN["run_huginn"]


class RecordedHuginnTools:
    """Replay the one persisted tool result captured for a PR fixture."""

    def __init__(self, run: dict[str, object]) -> None:
        """Keep a saved result and count calls to the two allowed tools."""
        self._run = run
        self.start_count = 0
        self.result_count = 0
        self.start_arguments: tuple[int, str, str, str] | None = None

    async def start_repro(
        self, installation_id: int, pr_id: str, head_sha: str, test_cmd: str
    ) -> dict[str, str | int]:
        """Return the fixture's run id as if the scoped MCP tool queued it."""
        self.start_count += 1
        self.start_arguments = (installation_id, pr_id, head_sha, test_cmd)
        return {
            "run_id": str(self._run["run_id"]),
            "attempt": int(str(self._run["attempt"])),
            "head_sha": head_sha,
            "status": "queued",
        }

    async def get_repro(self, installation_id: int, run_id: str) -> dict[str, object]:
        """Return the single persisted result recorded in the fixture."""
        self.result_count += 1
        return self._run


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
        sleep=lambda _delay: asyncio.sleep(0),
    )

    expected_verdict = fixture["expected_verdict"]
    expected = Verdict(str(expected_verdict)) if expected_verdict is not None else None
    assert result.status == ReproStatus(str(run["status"]))
    assert result.verdict == expected
    assert tools.start_count == 1 <= MAX_REPRO_RUNS
    assert tools.result_count == 1 <= MAX_TURNS
    assert tools.start_arguments is not None
    assert tools.start_arguments == (123, str(pr_id), str(run["head_sha"]), configured_command)
    assert configured_command == "pytest -q"
    assert str(fixture["pr_body"]) not in " ".join(map(str, tools.start_arguments))
