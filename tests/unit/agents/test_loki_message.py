"""Tests for the short room message Loki posts after saving its review."""

import pytest

from agents.loki._message import format_review_message


def test_no_findings_says_so() -> None:
    message = format_review_message(saved=0, rejected=0, head_sha="a" * 40)

    assert message == "Loki: reviewed aaaaaaa. No findings."


def test_saved_findings_are_counted_with_the_right_plural() -> None:
    assert "1 finding saved" in format_review_message(saved=1, rejected=0, head_sha="b" * 40)
    assert "3 findings saved" in format_review_message(saved=3, rejected=0, head_sha="b" * 40)


def test_rejected_findings_are_reported_so_a_quiet_review_is_not_mistaken_for_a_clean_one() -> None:
    message = format_review_message(saved=0, rejected=2, head_sha="c" * 40)

    assert "2 rejected" in message
    assert "No findings" not in message


@pytest.mark.parametrize("head_sha", ["a" * 40, "abc1234def"])
def test_message_names_the_short_head_and_nothing_from_the_pr(head_sha: str) -> None:
    message = format_review_message(saved=1, rejected=0, head_sha=head_sha)

    assert head_sha[:7] in message
    assert head_sha not in message or len(head_sha) <= 7
    assert message.startswith("Loki: ")
