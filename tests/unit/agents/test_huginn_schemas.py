"""Huginn's submit schema, its run cross-check and its room message."""

import pytest
from pydantic import ValidationError

from agents._guardrails import filter_tone
from agents.huginn._message import render_verdict
from agents.huginn.schemas import (
    MAX_FAILING_ASSERTION_CHARS,
    MAX_SUMMARY_CHARS,
    SUBMIT_VERDICT,
    HuginnVerdict,
    verdict_problems,
)

HEAD_SHA = "head-1"
INSTALLATION_ID = 42
RUN_IDS = tuple(f"00000000-0000-0000-0000-00000000000{index}" for index in range(1, 4))
ASSERTION = "tests/test_bug.py::test_regression: assert result == expected"


def _arguments(**overrides: object) -> dict[str, object]:
    """Build a valid submission, staying valid unless a test overrides a value."""
    arguments: dict[str, object] = {
        "verdict": "reproduced",
        "test_cmd": "pytest -q",
        "repro_ids": list(RUN_IDS),
        "with_fix": {"passed": 3, "failed": 0, "errors": 0},
        "without_fix": {"passed": 0, "failed": 3, "errors": 0},
        "failing_assertion": ASSERTION,
        "summary": "The fix makes the failing test pass.",
    }
    arguments.update(overrides)
    return arguments


def _verdict(**overrides: object) -> HuginnVerdict:
    """Build a verdict from the valid arguments plus ``overrides``."""
    return HuginnVerdict.model_validate(_arguments(**overrides))


class FakeRuns:
    """Serve saved runs by id, the way the tenant-scoped MCP tool does."""

    def __init__(self, runs: dict[str, dict[str, object]]) -> None:
        self._runs = runs

    async def get_repro(self, installation_id: int, run_id: str) -> dict[str, object]:
        """Return the saved run for ``run_id``."""
        assert installation_id == INSTALLATION_ID
        return self._runs[run_id]


def _run(head_sha: str = HEAD_SHA, *, with_fix: bool, without_fix: bool) -> dict[str, object]:
    """Build one saved run record in the shape ``get_repro`` returns."""
    return {
        "head_sha": head_sha,
        "with_fix": {"passed": with_fix, "failures": []},
        "without_fix": {"passed": without_fix, "failures": []},
    }


def _matching_runs() -> FakeRuns:
    """Serve three finished runs whose paired outcomes match a ``reproduced`` verdict."""
    return FakeRuns({run_id: _run(with_fix=True, without_fix=False) for run_id in RUN_IDS})


# --- schema ---------------------------------------------------------------------------


def test_schema_rejects_an_extra_field() -> None:
    with pytest.raises(ValidationError, match="notes"):
        _verdict(notes="not part of the schema")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("summary", "s" * (MAX_SUMMARY_CHARS + 1)),
        ("failing_assertion", "f" * (MAX_FAILING_ASSERTION_CHARS + 1)),
        ("repro_ids", [f"run-{index}" for index in range(1, 5)]),
    ],
)
def test_schema_rejects_values_over_the_limits(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _verdict(**{field: value})


def test_schema_rejects_an_unknown_verdict() -> None:
    with pytest.raises(ValidationError):
        _verdict(verdict="looks_even")


def test_counts_must_cover_every_named_run() -> None:
    with pytest.raises(ValidationError, match="with_fix"):
        _verdict(with_fix={"passed": 2, "failed": 0, "errors": 0})


def test_submit_tool_publishes_the_forbidden_extras_schema() -> None:
    assert SUBMIT_VERDICT.name == "submit_verdict"
    assert SUBMIT_VERDICT.spec.input_schema["additionalProperties"] is False


def test_tone_filter_cleans_the_prose_fields() -> None:
    verdict = _verdict(
        summary="Looks AI-generated.",
        failing_assertion="`load()` reads the file twice. The author clearly didn't test this.",
    )

    cleaned = verdict.filtered(filter_tone)

    assert cleaned.summary == ""
    assert cleaned.failing_assertion == "`load()` reads the file twice."


# --- cross-check against get_repro ------------------------------------------------------


async def test_matching_runs_raise_no_problems() -> None:
    problems = await verdict_problems(_matching_runs(), INSTALLATION_ID, _verdict(), HEAD_SHA)

    assert problems == ()


async def test_a_count_that_contradicts_get_repro_is_reported() -> None:
    verdict = _verdict(with_fix={"passed": 2, "failed": 0, "errors": 1})

    problems = await verdict_problems(_matching_runs(), INSTALLATION_ID, verdict, HEAD_SHA)

    assert problems == ("with_fix: submitted 2/0/1 but get_repro reported 3/0/0",)


async def test_a_run_for_another_head_is_refused() -> None:
    runs = {run_id: _run(with_fix=True, without_fix=False) for run_id in RUN_IDS}
    runs[RUN_IDS[0]] = _run("other-head", with_fix=True, without_fix=False)

    problems = await verdict_problems(FakeRuns(runs), INSTALLATION_ID, _verdict(), HEAD_SHA)

    assert problems == (f"repro_ids: {RUN_IDS[0]} is not a run for this PR head",)


# --- renderer ---------------------------------------------------------------------------


def test_render_verdict_uses_only_validated_fields() -> None:
    message = render_verdict(_verdict())

    assert message.splitlines() == [
        "Huginn: the paired tests reproduce the issue.",
        "Test command: pytest -q",
        "Runs: 3.",
        "With fix: 3 passed, 0 failed, 0 errors; without fix: 0 passed, 3 failed, 0 errors.",
        f"Failing assertion: {ASSERTION}",
        "The fix makes the failing test pass.",
    ]


def test_render_verdict_collapses_prose_to_one_line() -> None:
    message = render_verdict(_verdict(summary="first line\nsecond line", failing_assertion="a\nb"))

    assert message.splitlines()[-1] == "first line second line"
    assert "Failing assertion: a b" in message
