"""Tone filter: findings describe code, never the person who wrote it."""

import pytest

from agents._tone import filter_tone


@pytest.mark.parametrize(
    "sentence",
    [
        "The author clearly didn't test this.",
        "You forgot to handle None.",
        "This looks AI-generated.",
        "This code was probably written by ChatGPT.",
        "Probably copy-pasted from Stack Overflow.",
        "Sloppy work by the contributor.",
        "The contributor seems inexperienced.",
        "It looks like you rushed this change.",
        "Whoever wrote this did not understand async.",
    ],
)
def test_sentence_about_the_person_or_how_code_was_written_is_dropped(sentence: str) -> None:
    assert filter_tone(sentence) == ""


@pytest.mark.parametrize(
    "sentence",
    [
        "The `author` field is unset when the request is anonymous.",
        "`parse()` returns None on empty input.",
        "Callers should handle None.",
        "The PR adds a retry loop around `fetch()`.",
        "This change removes the only test for `merge()`.",
        "If you call `close()` twice, it raises `ValueError`.",
        "The generated client in `api/gen.py` is out of date.",
    ],
)
def test_sentence_about_code_is_kept(sentence: str) -> None:
    assert filter_tone(sentence) == sentence


def test_mixed_text_keeps_only_the_code_sentences() -> None:
    text = (
        "`load()` reads the file twice. The author clearly didn't test this. "
        "Passing an empty path raises `KeyError`."
    )

    assert filter_tone(text) == (
        "`load()` reads the file twice. Passing an empty path raises `KeyError`."
    )


def test_line_breaks_between_kept_sentences_survive() -> None:
    text = "`a()` leaks a handle.\nLooks AI-generated.\n`b()` never closes it."

    assert filter_tone(text) == "`a()` leaks a handle.\n`b()` never closes it."


def test_blank_text_stays_blank() -> None:
    assert filter_tone("   ") == ""
