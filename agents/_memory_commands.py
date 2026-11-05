"""Read a maintainer's ``remember`` / ``forget`` command to an agent and word the replies.

Notes are written only from these commands, parsed by plain code, never by a model: PR text
can steer a model, but it cannot type a command into the room as a maintainer. Replies are
fixed text that may name a validated key, and never repeat the note itself.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from agents._presence import strip_leading_mentions
from heimdall.modules.memory.api import AgentNote
from heimdall.shared.agents import AgentName

_REMEMBER = re.compile(r"remember\s+(?P<key>[^:\s]+)\s*:\s*(?P<value>\S.*)", re.I | re.S)
_FORGET = re.compile(r"forget\s+(?P<key>\S+)", re.I)
_LIST_NOTES = re.compile(r"what\s+do\s+you\s+remember\s*[?!.]*", re.I)
_REMEMBER_VERB = "remember"
_FORGET_VERB = "forget"
_UNSAFE_NOTE_TEXT = re.compile(r"[@`]|\s+")

MAX_NOTES_REPLY_CHARS = 3000
"""Longest notes listing, so 50 full notes never become one wall of chat."""


@dataclass(frozen=True)
class Remember:
    """Keep ``value`` under ``key``; both are still unvalidated."""

    key: str
    value: str


@dataclass(frozen=True)
class Forget:
    """Drop the note under ``key``; still unvalidated."""

    key: str


@dataclass(frozen=True)
class ListNotes:
    """List the agent's notes for the PR's repo."""


@dataclass(frozen=True)
class Usage:
    """A memory verb whose arguments could not be read."""


MemoryCommand = Remember | Forget | ListNotes | Usage


def parse_memory_command(body: str) -> MemoryCommand | None:
    """Recognise ``remember <key>: <note>``, ``forget <key>`` or ``what do you remember``.

    Args:
        body: Untrusted message text, including the @mention that addressed the agent.

    Returns:
        The command; ``Usage`` when the message starts with a memory verb but its arguments
        are malformed; None when it is not a memory command at all.
    """
    rest = strip_leading_mentions(body)
    if _LIST_NOTES.fullmatch(rest):
        return ListNotes()
    words = rest.split(maxsplit=1)
    verb = words[0].casefold() if words else ""
    if verb == _REMEMBER_VERB:
        remember = _REMEMBER.fullmatch(rest)
        if remember is None:
            return Usage()
        return Remember(remember["key"], remember["value"].strip())
    if verb == _FORGET_VERB:
        forget = _FORGET.fullmatch(rest)
        return Usage() if forget is None else Forget(forget["key"])
    return None


def _name(agent: AgentName) -> str:
    """The agent's name as it opens a reply."""
    return agent.value.capitalize()


def format_noted_reply(agent: AgentName, key: str) -> str:
    """Confirm a note was kept; ``key`` must already be validated."""
    return f"{_name(agent)}: noted `{key}`. I'll recall it on this repo's PRs."


def format_forgot_reply(agent: AgentName, key: str) -> str:
    """Confirm a note was dropped; ``key`` must already be validated."""
    return f"{_name(agent)}: forgot `{key}`."


def format_no_such_note_reply(agent: AgentName, key: str) -> str:
    """Say there is no note to drop; ``key`` must already be validated."""
    return f"{_name(agent)}: I have no note called `{key}`."


def format_usage_reply(agent: AgentName) -> str:
    """Explain the memory commands to someone who mistyped one."""
    mention = f"@{agent.value}"
    return (
        f"{_name(agent)}: to save a note, write `{mention} remember <key>: <note>`; "
        f"to drop one, `{mention} forget <key>`; to see them, `{mention} what do you remember`."
    )


def format_notes_reply(agent: AgentName, notes: Sequence[AgentNote]) -> str:
    """List an agent's notes for one repo, safe to post: no pings, no stray markdown.

    Note values were written by maintainers but are still free text, so every ``@`` and
    backtick is replaced and line breaks are folded away. The list is cut at
    ``MAX_NOTES_REPLY_CHARS`` and says how many notes it left out.

    Args:
        agent: The agent answering.
        notes: The notes to list, already ordered.

    Returns:
        The listing, or a line saying there are none and how to add one.
    """
    if not notes:
        return (
            f"{_name(agent)}: I have no notes for this repo. "
            f"A maintainer can add one with `@{agent.value} remember <key>: <note>`."
        )
    noun = "note" if len(notes) == 1 else "notes"
    header = f"{_name(agent)}: I have {len(notes)} {noun} for this repo:"
    lines = [f"- `{note.key}`: {_plain(note.value)}" for note in notes]
    return _cut(header, lines)


def _plain(text: str) -> str:
    """Fold whitespace to single spaces and defuse pings and code spans."""
    return _UNSAFE_NOTE_TEXT.sub(_defuse, text).strip()


def _defuse(match: re.Match[str]) -> str:
    return "" if match.group() == "@" else "'" if match.group() == "`" else " "


def _cut(header: str, lines: Sequence[str]) -> str:
    """Join the header and as many lines as fit, ending with how many were left out."""
    kept: list[str] = []
    length = len(header)
    for index, line in enumerate(lines):
        left_out = len(lines) - index
        ending = f"\n- …and {left_out} more."
        if length + 1 + len(line) + len(ending) > MAX_NOTES_REPLY_CHARS:
            return "\n".join([header, *kept]) + f"\n- …and {left_out} more."
        kept.append(line)
        length += 1 + len(line)
    return "\n".join([header, *kept])


def format_outside_thread_reply(agent: AgentName) -> str:
    """Ask for the command in a PR's thread, which is how the note's repo is known."""
    return (
        f"{_name(agent)}: send memory commands in a PR's thread, so I know which repo they're for."
    )


def format_invalid_reply(agent: AgentName, rule: str) -> str:
    """Refuse a note that breaks a memory rule.

    Args:
        agent: The agent answering.
        rule: The ``InvalidNote`` message; fixed text from the memory module, never user input.

    Returns:
        The refusal naming the rule.
    """
    return f"{_name(agent)}: I can't keep that note: {rule}."
