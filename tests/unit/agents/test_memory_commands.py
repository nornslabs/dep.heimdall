"""Reading a maintainer's remember/forget command, and the fixed replies to it."""

import re
from datetime import UTC, datetime

import pytest

from agents._memory_commands import (
    MAX_NOTES_REPLY_CHARS,
    Forget,
    ListNotes,
    Remember,
    Usage,
    format_forgot_reply,
    format_invalid_reply,
    format_no_such_note_reply,
    format_noted_reply,
    format_notes_reply,
    format_outside_thread_reply,
    format_usage_reply,
    parse_memory_command,
)
from heimdall.modules.memory.api import AgentNote
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


@pytest.mark.parametrize(
    "body",
    [
        "@loki what do you remember",
        "@loki What do you remember?",
        "@huginn   what  do you  remember ?!",
        "@heimdall WHAT DO YOU REMEMBER.",
    ],
)
def test_asking_what_the_agent_remembers_is_read_as_a_listing(body: str) -> None:
    assert parse_memory_command(body) == ListNotes()


@pytest.mark.parametrize(
    "body",
    [
        "@loki what do you remember about tests?",
        "@loki what do you remember from last week",
        "@loki what do you remembers",
    ],
)
def test_a_longer_question_that_starts_the_same_way_is_an_ordinary_mention(body: str) -> None:
    assert parse_memory_command(body) is None


def _note(key: str, value: str) -> AgentNote:
    return AgentNote(
        key=key,
        value=value,
        created_by="alice",
        source_message_id="msg-1",
        created_at=datetime(2026, 11, 5, tzinfo=UTC),
        expires_at=None,
    )


def test_the_notes_reply_lists_every_note_with_its_key() -> None:
    reply = format_notes_reply(
        AgentName.LOKI,
        (_note("scope", "docs/ is out of scope"), _note("test-cmd", "pytest -q")),
    )

    assert reply == (
        "Loki: I have 2 notes for this repo:\n"
        "- `scope`: docs/ is out of scope\n"
        "- `test-cmd`: pytest -q"
    )


def test_a_single_note_reads_in_the_singular() -> None:
    reply = format_notes_reply(AgentName.HUGINN, (_note("scope", "docs/"),))

    assert reply.startswith("Huginn: I have 1 note for this repo:\n")


def test_the_notes_reply_for_no_notes_says_how_to_add_one() -> None:
    assert format_notes_reply(AgentName.LOKI, ()) == (
        "Loki: I have no notes for this repo. "
        "A maintainer can add one with `@loki remember <key>: <note>`."
    )


def test_a_note_value_cannot_ping_anyone_or_break_the_formatting() -> None:
    hostile = _note("scope", "@channel `rm -rf`\nsecond line @alice")

    reply = format_notes_reply(AgentName.LOKI, (hostile,))

    assert "@" not in reply
    assert reply.count("`") == 2
    assert reply.count("\n") == 1
    assert "channel 'rm -rf' second line" in reply


def test_a_long_list_is_cut_and_says_how_many_notes_were_left_out() -> None:
    notes = tuple(_note(f"key-{index:02d}", "x" * 300) for index in range(50))

    reply = format_notes_reply(AgentName.LOKI, notes)

    left_out_match = re.search(r"and (\d+) more\.$", reply)
    assert left_out_match is not None
    shown = reply.count("\n- `")
    assert len(reply) <= MAX_NOTES_REPLY_CHARS
    assert 0 < shown < 50
    assert shown + int(left_out_match[1]) == 50
    assert "I have 50 notes for this repo:" in reply


def test_usage_and_outside_thread_replies_explain_what_to_do() -> None:
    assert "`@huginn remember <key>: <note>`" in format_usage_reply(AgentName.HUGINN)
    assert "`@huginn forget <key>`" in format_usage_reply(AgentName.HUGINN)
    assert "`@huginn what do you remember`" in format_usage_reply(AgentName.HUGINN)
    assert "PR's thread" in format_outside_thread_reply(AgentName.HUGINN)


def test_an_invalid_note_reply_carries_the_rule_it_broke() -> None:
    assert format_invalid_reply(AgentName.LOKI, "note value must not be empty") == (
        "Loki: I can't keep that note: note value must not be empty."
    )
