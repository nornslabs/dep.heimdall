"""Helpers for messages addressed to an agent: mention stripping, refusals and room claiming."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from agents._presence import (
    format_not_maintainer_reply,
    format_rate_limited_reply,
    strip_leading_mentions,
)
from agents._presence_runner import claimable_rooms
from heimdall.shared.agents import AgentName


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("@huginn why did it fail?", "why did it fail?"),
        ("@Huginn: hello", "hello"),
        ("@huginn @loki @heimdall status", "status"),
        ("  @loki   remember a: b ", "remember a: b"),
        ("no mention here", "no mention here"),
        ("see @huginn later", "see @huginn later"),
        ("@odin ping", "@odin ping"),
    ],
)
def test_only_the_run_of_agent_mentions_that_opens_a_message_is_stripped(
    body: str, expected: str
) -> None:
    assert strip_leading_mentions(body) == expected


def test_rooms_to_claim_join_the_linked_ones_with_the_default_without_repeats() -> None:
    rooms = claimable_rooms(["room-b", "room-a", "room-b"], "room-c")

    assert rooms == ("room-a", "room-b", "room-c")


def test_rooms_to_claim_skip_a_blank_default_and_blank_links() -> None:
    assert claimable_rooms(["room-a", ""], None) == ("room-a",)
    assert claimable_rooms([], "  ") == ()


def test_rooms_to_claim_do_not_repeat_a_default_that_is_already_linked() -> None:
    assert claimable_rooms(["room-a"], "room-a") == ("room-a",)


def test_the_not_a_maintainer_reply_names_the_sender_and_says_what_to_ask_the_admin() -> None:
    reply = format_not_maintainer_reply(AgentName.HUGINN, "priyankar")

    assert reply.startswith("Huginn: ")
    assert "@priyankar" in reply
    assert "add `priyankar` to `maintainers` in `heimdall.yml`" in reply


@pytest.mark.parametrize(
    ("sender_name", "expected"),
    [
        ("Alice", "Alice"),
        ("a.b_c-d9", "a.b_c-d9"),
        ("@channel", "channel"),
        ("bob `rm -rf /`", "bobrm-rf"),
        ("eve\n@here", "evehere"),
    ],
)
def test_the_sender_name_is_cleaned_before_it_goes_into_the_reply(
    sender_name: str, expected: str
) -> None:
    reply = format_not_maintainer_reply(AgentName.LOKI, sender_name)

    assert f"@{expected}," in reply
    assert f"add `{expected}` to" in reply
    assert reply.count("@") == 1


def test_the_rate_limit_notice_names_the_agent_and_the_time_to_try_again() -> None:
    retry_after = datetime(2026, 11, 5, 13, 7, 45, tzinfo=UTC)

    reply = format_rate_limited_reply(AgentName.LOKI, retry_after)

    assert reply == (
        "Loki: you're rate-limited for now, so I'll stay quiet; try again after 13:07 UTC."
    )


def test_the_rate_limit_notice_mentions_nobody() -> None:
    reply = format_rate_limited_reply(AgentName.HUGINN, datetime(2026, 11, 5, 9, 0, tzinfo=UTC))

    assert "@" not in reply


def test_the_rate_limit_notice_shows_the_time_in_utc() -> None:
    plus_two = timezone(timedelta(hours=2))

    reply = format_rate_limited_reply(
        AgentName.LOKI, datetime(2026, 11, 5, 15, 30, tzinfo=plus_two)
    )

    assert "13:30 UTC" in reply


@pytest.mark.parametrize("sender_name", ["", "@@@", "`  `", "名前"])
def test_a_sender_name_with_nothing_usable_gets_a_reply_without_a_name(sender_name: str) -> None:
    reply = format_not_maintainer_reply(AgentName.HEIMDALL, sender_name)

    assert reply.startswith("Heimdall: ")
    assert "@" not in reply
    assert "add your username to `maintainers` in `heimdall.yml`" in reply
