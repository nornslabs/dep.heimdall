"""Tests for the shell-free GitHub Actions reproduction runner."""

import json
import runpy
import subprocess
from pathlib import Path
from typing import TextIO, cast

RUNNER = Path(__file__).resolve().parents[3] / ".github/scripts/run-repro.py"


def test_runner_invokes_configured_args_without_a_shell(monkeypatch, tmp_path: Path) -> None:
    """A configured command is tokenized and executed as argv, never shell text."""
    module = runpy.run_path(str(RUNNER))
    calls: list[dict[str, object]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append({"command": command, **kwargs})
        cast(TextIO, kwargs["stdout"]).write("1 passed\n")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("sys.argv", [str(RUNNER), "with-fix"])
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("TEST_COMMAND", "pytest -q")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert module["main"]() == 0

    assert calls[0]["command"] == ["pytest", "-q"]
    assert "shell" not in calls[0]
    outcome = json.loads((tmp_path / "heimdall-repro/with-fix.json").read_text())
    assert outcome == {"passed": True, "failures": []}


def test_runner_keeps_shell_metacharacters_inside_an_argument(monkeypatch, tmp_path: Path) -> None:
    """Shell syntax stays inert because the command is passed as an argument list."""
    module = runpy.run_path(str(RUNNER))
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("sys.argv", [str(RUNNER), "without-fix"])
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("TEST_COMMAND", "pytest -q; touch /tmp/should-not-exist")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert module["main"]() == 0

    assert commands == [["pytest", "-q;", "touch", "/tmp/should-not-exist"]]
    outcome = json.loads((tmp_path / "heimdall-repro/without-fix.json").read_text())
    assert outcome == {"passed": True, "failures": []}
