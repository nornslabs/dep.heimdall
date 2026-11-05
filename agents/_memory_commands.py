"""Read a maintainer's ``remember`` / ``forget`` command to an agent and word the replies.

Notes are written only from these commands, parsed by plain code, never by a model: PR text
can steer a model, but it cannot type a command into the room as a maintainer. Replies are
fixed text that may name a validated key, and never repeat the note itself.
"""

import re
from dataclasses import dataclass

from agents._presence import strip_leading_mentions
from heimdall.shared.agents import AgentName

_REMEMBER = re.compile(r"remember\s+(?P<key>[^:\s]+)\s*:\s*(?P<value>\S.*)", re.I | re.S)
_FORGET = re.compile(r"forget\s+(?P<key>\S+)", re.I)
_REMEMBER_VERB = "remember"
_FORGET_VERB = "forget"


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
class Usage:
    """A memory verb whose arguments could not be read."""


MemoryCommand = Remember | Forget | Usage


def parse_memory_command(body: str) -> MemoryCommand | None:
    """Recognise ``remember <key>: <note>`` or ``forget <key>`` after the agent mentions.

    Args:
        body: Untrusted message text, including the @mention that addressed the agent.

    Returns:
        The command; ``Usage`` when the message starts with a memory verb but its arguments
        are malformed; None when it is not a memory command at all.
    """
    rest = strip_leading_mentions(body)
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
    """Explain the two memory commands to someone who mistyped one."""
    mention = f"@{agent.value}"
    return (
        f"{_name(agent)}: to save a note, write `{mention} remember <key>: <note>`; "
        f"to drop one, `{mention} forget <key>`."
    )


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
