"""Pure rules for agent notes: what a valid key and value look like, and how recall matches."""

from datetime import UTC, datetime

import pytest

from heimdall.modules.memory.api import (
    MAX_NOTE_VALUE_CHARS,
    AgentNote,
    NoteInput,
    matches_query,
    validate_key,
    validate_note,
)
from heimdall.shared.errors import InvalidNote


def _note(key: str, value: str) -> AgentNote:
    return AgentNote(
        key=key,
        value=value,
        created_by="alice",
        source_message_id="msg-1",
        created_at=datetime(2026, 11, 5, tzinfo=UTC),
        expires_at=None,
    )


def test_a_valid_note_is_returned_trimmed_with_a_lowercase_key() -> None:
    assert validate_note("  Test-Command ", " pytest -q tests/unit ") == NoteInput(
        key="test-command", value="pytest -q tests/unit"
    )


def test_a_value_at_the_limit_is_accepted() -> None:
    value = "x" * MAX_NOTE_VALUE_CHARS

    assert validate_note("limit", value).value == value


def test_a_value_over_the_limit_is_refused() -> None:
    with pytest.raises(InvalidNote, match="300"):
        validate_note("limit", "x" * (MAX_NOTE_VALUE_CHARS + 1))


@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_value_is_refused(value: str) -> None:
    with pytest.raises(InvalidNote):
        validate_note("scope", value)


@pytest.mark.parametrize("key", ["", "-starts-with-dash", "has space", "semi;colon", "k" * 65])
def test_a_badly_formed_key_is_refused(key: str) -> None:
    with pytest.raises(InvalidNote):
        validate_key(key)


def test_a_key_of_64_characters_is_accepted() -> None:
    assert validate_key("k" * 64) == "k" * 64


def test_an_empty_query_matches_every_note() -> None:
    assert matches_query(_note("scope", "docs/ is out of scope"), "")


def test_a_query_matches_the_key_ignoring_case() -> None:
    assert matches_query(_note("test-command", "pytest -q"), "TEST")


def test_a_query_matches_the_value_ignoring_case() -> None:
    assert matches_query(_note("scope", "Ignore docs/ in review"), "ignore DOCS")


def test_a_query_found_in_neither_key_nor_value_does_not_match() -> None:
    assert not matches_query(_note("scope", "docs/ is out of scope"), "pytest")
