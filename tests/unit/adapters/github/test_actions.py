"""Validate result artifacts before recording workflow outcomes."""

import io
import json
import zipfile

import pytest

from heimdall.adapters.github.actions import parse_artifact


def _archive(run_id: str, *, marker: str = "assert result == 1") -> bytes:
    """Build the small artifact contract used by the reproduction workflow."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zipped:
        zipped.writestr("run-id", run_id)
        zipped.writestr(
            "with-fix.json",
            json.dumps({"passed": False, "failures": [marker], "duration_seconds": 1.25}),
        )
        zipped.writestr(
            "without-fix.json",
            json.dumps({"passed": False, "failures": [marker], "duration_seconds": 0.75}),
        )
    return output.getvalue()


def test_parse_artifact_returns_paired_outcomes_and_failure_evidence() -> None:
    """Preserve bounded test assertions from both variants."""
    artifact = parse_artifact(_archive("run-1"), "run-1")
    assert artifact.run_id == "run-1"
    assert artifact.with_fix.passed is False
    assert artifact.with_fix.failures == ("assert result == 1",)
    assert artifact.without_fix.duration_seconds == 0.75


def test_parse_artifact_rejects_a_different_embedded_run_id() -> None:
    """Do not attach an artifact to a run other than its embedded identity."""
    with pytest.raises(ValueError, match="another run"):
        parse_artifact(_archive("run-a"), "run-b")


def test_parse_artifact_rejects_unbounded_or_missing_files() -> None:
    """Require the exact minimal files needed to judge a paired attempt."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zipped:
        zipped.writestr("run-id", "run-1")
    with pytest.raises(ValueError, match="missing required files"):
        parse_artifact(output.getvalue(), "run-1")
