"""Run a configured reproduction command without invoking a shell."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

MAX_FAILURE_LINES = 20
MAX_FAILURE_LINE_LENGTH = 1000
COMMAND_TIMEOUT_SECONDS = 25 * 60
ALLOWED_COMMANDS = {("pytest",), ("npm", "test"), ("go", "test")}


def main() -> int:
    """Run one configured test command and write its bounded outcome."""
    variant = sys.argv[1]
    runner_temp = Path(os.environ["RUNNER_TEMP"])
    command = shlex.split(os.environ["TEST_COMMAND"])
    if not _allowed_command(command):
        return _write_failure(variant, runner_temp, "Unsupported configured test runner")

    log_path = runner_temp / f"{variant}.log"
    try:
        with log_path.open("w", encoding="utf-8") as log:
            result = subprocess.run(
                command,
                check=False,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
    except (OSError, subprocess.TimeoutExpired) as error:
        if isinstance(error, subprocess.TimeoutExpired):
            reason = "Test command timed out"
        else:
            reason = "Test runner could not start"
        return _write_failure(variant, runner_temp, reason)

    failures = _failure_lines(log_path)
    outcome = {"passed": result.returncode == 0, "failures": failures}
    _write_outcome(variant, runner_temp, outcome)
    return 0


def _allowed_command(command: list[str]) -> bool:
    """Accept only the three runner forms permitted by repository config."""
    return any(tuple(command[: len(prefix)]) == prefix for prefix in ALLOWED_COMMANDS)


def _failure_lines(log_path: Path) -> list[str]:
    """Keep likely assertion and failure lines without uploading the full log."""
    terms = ("assert", "failed", "error", "failure")
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    matching = [
        line[-MAX_FAILURE_LINE_LENGTH:]
        for line in lines
        if any(term in line.lower() for term in terms)
    ]
    return matching[-MAX_FAILURE_LINES:]


def _write_failure(variant: str, runner_temp: Path, reason: str) -> int:
    """Save a failed outcome when the runner cannot produce a test result."""
    _write_outcome(variant, runner_temp, {"passed": False, "failures": [reason]})
    return 0


def _write_outcome(variant: str, runner_temp: Path, outcome: dict[str, object]) -> None:
    """Persist one JSON result for the paired artifact builder."""
    output_dir = runner_temp / "heimdall-repro"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{variant}.json").write_text(json.dumps(outcome), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
