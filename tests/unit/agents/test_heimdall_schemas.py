"""Heimdall's submit schema, its saved-id check and its room message."""

import pytest
from pydantic import ValidationError

from agents._guardrails import filter_tone
from agents.heimdall._message import EMPTY_EXPLANATION_MESSAGE, render_desk_result
from agents.heimdall.schemas import (
    MAX_EXPLANATION_CHARS,
    SUBMIT_RESULT,
    DeskResult,
    desk_result_problems,
)

SAVED_MESSAGE = "## PR #7: Fix\n\n<!-- heimdall-approval draft_id=draft-1 -->"


# --- schema ---------------------------------------------------------------------------


def test_schema_rejects_an_extra_field() -> None:
    with pytest.raises(ValidationError, match="summary"):
        DeskResult.model_validate({"action": "card", "card_id": "card-1", "summary": "extra"})


def test_schema_requires_the_field_the_action_names() -> None:
    with pytest.raises(ValidationError, match="card_id is required"):
        DeskResult.model_validate({"action": "card"})


def test_schema_rejects_a_field_belonging_to_another_action() -> None:
    with pytest.raises(ValidationError, match="draft_id must not be set"):
        DeskResult.model_validate({"action": "card", "card_id": "card-1", "draft_id": "draft-1"})


def test_schema_rejects_an_over_long_explanation() -> None:
    with pytest.raises(ValidationError):
        DeskResult.model_validate(
            {"action": "explanation", "explanation": "e" * (MAX_EXPLANATION_CHARS + 1)}
        )


def test_submit_tool_publishes_the_forbidden_extras_schema() -> None:
    assert SUBMIT_RESULT.name == "submit_result"
    assert SUBMIT_RESULT.spec.input_schema["additionalProperties"] is False


def test_tone_filter_cleans_the_explanation_only() -> None:
    result = DeskResult.model_validate(
        {"action": "explanation", "explanation": "Looks AI-generated."}
    )

    assert result.filtered(filter_tone).explanation == ""
    assert DeskResult.model_validate({"action": "card", "card_id": "card-1"}).filtered(
        filter_tone
    ) == DeskResult.model_validate({"action": "card", "card_id": "card-1"})


# --- cross-check against the saved ids --------------------------------------------------


def test_desk_result_problems_accepts_an_id_this_run_saved() -> None:
    result = DeskResult.model_validate({"action": "draft", "draft_id": "draft-1"})

    assert desk_result_problems(result, ["draft-0", "draft-1"]) == ()


def test_desk_result_problems_names_an_id_this_run_did_not_save() -> None:
    result = DeskResult.model_validate({"action": "mechanical_task", "task_id": "task-9"})

    assert desk_result_problems(result, ["task-1"]) == (
        "task_id: task-9 was not saved for this PR head",
    )


def test_desk_result_problems_skips_explanations() -> None:
    result = DeskResult.model_validate({"action": "explanation", "explanation": "Why."})

    assert desk_result_problems(result, []) == ()


# --- renderer ---------------------------------------------------------------------------


def test_render_desk_result_posts_the_explanation() -> None:
    result = DeskResult.model_validate(
        {"action": "explanation", "explanation": "The retry is bounded by MAX_POLLS."}
    )

    assert render_desk_result(result) == "Heimdall: The retry is bounded by MAX_POLLS."


def test_render_desk_result_reports_an_explanation_the_filter_emptied() -> None:
    result = DeskResult.model_validate(
        {"action": "explanation", "explanation": "Looks AI-generated."}
    ).filtered(filter_tone)

    assert render_desk_result(result) == EMPTY_EXPLANATION_MESSAGE


def test_render_desk_result_posts_the_trusted_text_for_saved_work() -> None:
    result = DeskResult.model_validate({"action": "card", "card_id": "card-1"})

    assert render_desk_result(result, SAVED_MESSAGE) == SAVED_MESSAGE


def test_render_desk_result_requires_the_trusted_text_for_saved_work() -> None:
    result = DeskResult.model_validate({"action": "card", "card_id": "card-1"})

    with pytest.raises(ValueError, match="saved_message"):
        render_desk_result(result)
