"""Loki's submit schema, its finding-id check and its room message."""

import pytest
from pydantic import ValidationError

from agents._guardrails import filter_tone
from agents.loki._message import render_review
from agents.loki.schemas import MAX_SUMMARY_CHARS, SUBMIT_REVIEW, LokiReview, review_problems

HEAD_SHA = "a" * 40
FINDING_IDS = ("finding-1", "finding-2")


def _review(**overrides: object) -> LokiReview:
    """Build a valid review, staying valid unless a test overrides a value."""
    arguments: dict[str, object] = {
        "finding_ids": list(FINDING_IDS),
        "summary": "Two unchecked failure paths.",
    }
    arguments.update(overrides)
    return LokiReview.model_validate(arguments)


# --- schema ---------------------------------------------------------------------------


def test_schema_rejects_an_extra_field() -> None:
    with pytest.raises(ValidationError, match="head_sha"):
        _review(head_sha=HEAD_SHA)


def test_schema_rejects_more_than_five_findings() -> None:
    with pytest.raises(ValidationError):
        _review(finding_ids=[f"finding-{index}" for index in range(1, 7)])


def test_schema_rejects_an_over_long_summary() -> None:
    with pytest.raises(ValidationError):
        _review(summary="s" * (MAX_SUMMARY_CHARS + 1))


def test_skip_reason_and_findings_cannot_both_be_set() -> None:
    with pytest.raises(ValidationError, match="cannot both be set"):
        _review(skipped_reason="no_findings")


def test_a_review_needs_findings_or_a_skip_reason() -> None:
    with pytest.raises(ValidationError, match="skipped_reason is required"):
        _review(finding_ids=[])


def test_submit_tool_publishes_the_forbidden_extras_schema() -> None:
    assert SUBMIT_REVIEW.name == "submit_review"
    assert SUBMIT_REVIEW.spec.input_schema["additionalProperties"] is False


def test_tone_filter_cleans_the_summary() -> None:
    review = _review(summary="Looks AI-generated.")

    assert review.filtered(filter_tone).summary == ""


# --- cross-check against save_findings --------------------------------------------------


def test_review_problems_names_ids_this_run_did_not_save() -> None:
    review = _review(finding_ids=["finding-1", "hallucinated"])

    problems = review_problems(review, ["finding-1"])

    assert problems == (
        "finding_ids: hallucinated was not saved through save_findings for this PR head",
    )


def test_review_problems_is_empty_when_every_id_was_saved() -> None:
    assert review_problems(_review(), list(FINDING_IDS)) == ()


def test_a_skip_reason_has_no_ids_to_check() -> None:
    assert review_problems(_review(finding_ids=[], skipped_reason="docs_only"), []) == ()


# --- renderer ---------------------------------------------------------------------------


def test_render_review_counts_findings_and_keeps_the_summary_on_one_line() -> None:
    review = _review(summary="Two unchecked\nfailure paths.")

    message = render_review(review, head_sha=HEAD_SHA, rejected=1)

    assert message == (
        f"Loki: reviewed {HEAD_SHA[:7]}. 2 findings saved, 1 rejected.\n"
        "Two unchecked failure paths."
    )


def test_render_review_uses_the_singular_noun() -> None:
    message = render_review(_review(finding_ids=["finding-1"]), head_sha=HEAD_SHA)

    assert message.startswith(f"Loki: reviewed {HEAD_SHA[:7]}. 1 finding saved.")


@pytest.mark.parametrize(
    ("skipped_reason", "expected"),
    [
        ("docs_only", f"Loki: reviewed {HEAD_SHA[:7]}. Docs-only change; no findings."),
        ("no_findings", f"Loki: reviewed {HEAD_SHA[:7]}. No findings."),
    ],
)
def test_render_review_says_why_there_are_no_findings(skipped_reason: str, expected: str) -> None:
    message = render_review(
        _review(finding_ids=[], skipped_reason=skipped_reason), head_sha=HEAD_SHA
    )

    assert message.splitlines()[0] == expected
