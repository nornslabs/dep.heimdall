"""Reading a maintainer's remember/forget command, and the fixed replies to it."""

import pytest

from agents._memory_commands import (
    Forget,
    Remember,
    Usage,
    format_forgot_reply,
    format_invalid_reply,
    format_no_such_note_reply,
    format_noted_reply,
    format_outside_thread_reply,
    format_usage_reply,
    parse_memory_command,
)
from heimdall.shared.agents import AgentName


def test_remember_with_key_and_note_is_read() -> None:
    assert parse_memory_command("@loki remember scope: docs/ is out of scope") == Remember(
        "scope", "docs/ is out of scope"
    )


def test_the_verb_ignores_case_and_the_note_keeps_its_colons() -> None:
    assert parse_memory_command("@huginn REMEMBER test-cmd: pytest -q -k 'a:b'") == Remember(
        "test-cmd", "pytest -q -k 'a:b'"
    )


def test_a_note_may_span_several_lines() -> None:
    assert parse_memory_command("@loki remember style:\nearly returns\nno nesting") == Remember(
        "style", "early returns\nno nesting"
    )


def test_forget_with_a_key_is_read() -> None:
    assert parse_memory_command("@loki Forget scope") == Forget("scope")


@pytest.mark.parametrize(
    "body",
    [
        "@loki remember docs/ is out of scope",
        "@loki remember",
        "@loki remember two words: note",
        "@loki remember scope:",
        "@loki forget",
        "@loki forget two keys",
    ],
)
def test_a_malformed_memory_command_asks_for_the_usage(body: str) -> None:
    assert isinstance(parse_memory_command(body), Usage)


@pytest.mark.parametrize(
    "body",
    ["@loki ping", "@loki what does this PR change?", "@loki remembering is hard", ""],
)
def test_anything_else_is_not_a_memory_command(body: str) -> None:
    assert parse_memory_command(body) is None


def test_replies_name_the_agent_and_key_but_never_the_note() -> None:
    assert format_noted_reply(AgentName.LOKI, "scope") == (
        "Loki: noted `scope`. I'll recall it on this repo's PRs."
    )
    assert format_forgot_reply(AgentName.LOKI, "scope") == "Loki: forgot `scope`."
    assert format_no_such_note_reply(AgentName.LOKI, "scope") == (
        "Loki: I have no note called `scope`."
    )


def test_usage_and_outside_thread_replies_explain_what_to_do() -> None:
    assert "`@huginn remember <key>: <note>`" in format_usage_reply(AgentName.HUGINN)
    assert "`@huginn forget <key>`" in format_usage_reply(AgentName.HUGINN)
    assert "PR's thread" in format_outside_thread_reply(AgentName.HUGINN)


def test_an_invalid_note_reply_carries_the_rule_it_broke() -> None:
    assert format_invalid_reply(AgentName.LOKI, "note value must not be empty") == (
        "Loki: I can't keep that note: note value must not be empty."
    )
