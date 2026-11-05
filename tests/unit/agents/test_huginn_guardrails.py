"""Huginn's own guardrails: allowed runner, only the command changes, three runs."""

from collections.abc import Mapping

import pytest

from agents._toolbox import AgentTool
from agents.huginn.guardrails import ReproGuard, guard_start_repro
from heimdall.adapters.ai import ToolSpec
from heimdall.modules.repro.api import MAX_REPRO_RUNS
from heimdall.shared.errors import ReproLimitReached


def _arguments(test_cmd: str = "pytest -q") -> dict[str, object]:
    """Build one ``start_repro`` argument set, valid unless a test overrides the command."""
    return {
        "installation_id": 42,
        "pr_id": "00000000-0000-0000-0000-000000000002",
        "head_sha": "head-1",
        "test_cmd": test_cmd,
    }


def test_a_configured_command_with_an_allowed_runner_is_accepted() -> None:
    assert ReproGuard(("pytest",)).refusal(_arguments()) is None


def test_a_command_that_replaces_the_runner_is_refused() -> None:
    refusal = ReproGuard(("pytest",)).refusal(_arguments("python -m pytest"))

    assert refusal is not None
    assert "'pytest'" in refusal


def test_a_narrowed_command_keeps_the_runner() -> None:
    guard = ReproGuard(("pytest",))

    assert guard.refusal(_arguments("pytest -q tests/test_pager.py")) is None


def test_the_runner_may_not_change_between_runs() -> None:
    guard = ReproGuard(("pytest", "npm test"))
    guard.record("pytest -q")

    refusal = guard.refusal(_arguments("npm test"))

    assert refusal is not None
    assert "only the command may change" in refusal


@pytest.mark.parametrize("command", ["pytest -q", "pytest -q tests/test_pager.py"])
def test_the_command_may_change_between_runs(command: str) -> None:
    guard = ReproGuard(("pytest",))
    guard.record("pytest -q")

    assert guard.refusal(_arguments(command)) is None


def test_a_fourth_run_is_refused() -> None:
    guard = ReproGuard(("pytest",))
    for _attempt in range(MAX_REPRO_RUNS):
        assert guard.refusal(_arguments()) is None
        guard.record("pytest -q")

    refusal = guard.refusal(_arguments())

    assert refusal is not None
    assert str(MAX_REPRO_RUNS) in refusal


async def test_a_refused_start_reaches_the_model_as_an_error_result() -> None:
    started: list[Mapping[str, object]] = []

    async def start(arguments: Mapping[str, object]) -> object:
        started.append(arguments)
        return {"run_id": "run-1"}

    tool = guard_start_repro(
        ReproGuard(("pytest", "npm test")),
        AgentTool(ToolSpec("start_repro", "Queue a run.", {"type": "object"}), start),
    )

    first = await tool.handler(_arguments())
    second = await tool.handler(_arguments("npm test"))

    assert first == {"run_id": "run-1"}
    assert second == "error: start_repro: only the command may change between runs, not the runner"
    assert len(started) == 1


def test_the_real_reproduction_cap_matches_the_guard() -> None:
    guard = ReproGuard(("pytest",))
    for _attempt in range(MAX_REPRO_RUNS):
        guard.record("pytest -q")

    with pytest.raises(ReproLimitReached):
        raise ReproLimitReached("head already has 3 repro runs")
