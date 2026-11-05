"""Recognise a maintainer's "are you there?" message to an agent and word the answer.

Message bodies are untrusted, so recognition is an exact match on a few words or short phrases
after the leading @mentions, with a length cap. Nothing from the body is ever copied into a
reply. The one thing a reply does echo is the sender's name, and only after it is cut down to
the characters a GitHub login can contain.
"""

import re
from enum import Enum

from heimdall.shared.agents import AgentName

MAX_PROMPT_LENGTH = 200
MAX_LOGIN_LENGTH = 39
_NOT_LOGIN_CHARACTERS = re.compile(r"[^A-Za-z0-9._-]")
_LEADING_MENTIONS = re.compile(
    "^\\s*(?:@(?:" + "|".join(name.value for name in AgentName) + ")\\b[:,]?\\s*)+",
    re.IGNORECASE,
)
_TRAILING_PUNCTUATION = " \t\r\n!?.:"
_PING_WORDS = frozenset({"ping", "alive", "status"})
_HELLO_WORDS = frozenset({"hello", "hi", "hey"})
_AWAKE_PHRASES = frozenset(
    {
        "are you awake",
        "you awake",
        "awake",
        "are you there",
        "you there",
    }
)
# Mirrors the Role line in each agent's prompt.md, so the reply reports the same role the model
# is given.
_ROLES = {
    AgentName.HUGINN: (
        "run the repository's configured reproduction command and report paired test outcomes"
    ),
    AgentName.LOKI: (
        "review the current pull request diff and report concrete, actionable findings"
    ),
    AgentName.HEIMDALL: (
        "summarize completed triage, present a Switch card, and route maintainer commands"
    ),
}


class PresencePrompt(Enum):
    """The kinds of presence prompt an agent answers."""

    PING = "ping"
    HELLO = "hello"
    AWAKE = "awake"


def strip_leading_mentions(body: str) -> str:
    """Drop the run of agent @mentions that opens a message, and surrounding whitespace.

    Args:
        body: Untrusted message text.

    Returns:
        What the sender asked, without the mentions that addressed the agents.
    """
    return _LEADING_MENTIONS.sub("", body, count=1).strip()


def parse_presence_prompt(body: str) -> PresencePrompt | None:
    """Decide whether a message is a bare ping, greeting or awake check.

    Args:
        body: Untrusted message text, including the @mention that addressed the agent.

    Returns:
        The kind of prompt, or None for anything else: extra words, other mentions, shell
        syntax, instructions, empty text or text longer than ``MAX_PROMPT_LENGTH``.
    """
    if len(body) > MAX_PROMPT_LENGTH:
        return None
    rest = _LEADING_MENTIONS.sub("", body, count=1)
    phrase = " ".join(rest.split()).rstrip(_TRAILING_PUNCTUATION).casefold()
    if phrase in _PING_WORDS:
        return PresencePrompt.PING
    if phrase in _HELLO_WORDS:
        return PresencePrompt.HELLO
    if phrase in _AWAKE_PHRASES:
        return PresencePrompt.AWAKE
    return None


def format_presence_reply(agent: AgentName, prompt: PresencePrompt) -> str:
    """Word the one-line answer that says the agent is running.

    Args:
        agent: The agent answering.
        prompt: What it was asked.

    Returns:
        Fixed text naming the agent and its role; it never contains the asker's words.
    """
    name = agent.value.capitalize()
    if prompt is PresencePrompt.PING:
        return f"{name}: pong, I'm alive."
    if prompt is PresencePrompt.AWAKE:
        return f"{name}: I'm awake. I {_ROLES[agent]}."
    return f"{name}: hello! I'm alive. I {_ROLES[agent]}."


def format_not_maintainer_reply(agent: AgentName, sender_name: str) -> str:
    """Word the answer to someone who pinged an agent but is not on the maintainer list.

    The sender's name comes from the chat platform, so it is untrusted: only letters, digits,
    ``.``, ``_`` and ``-`` survive, which keeps it from pinging a channel or injecting markdown.

    Args:
        agent: The agent answering.
        sender_name: Display name Switch reported for the sender.

    Returns:
        Fixed text naming the agent and telling the sender to ask an admin to add them to
        ``maintainers`` in ``heimdall.yml``. When nothing usable is left of the name, the text
        says "your username" and mentions nobody.
    """
    name = agent.value.capitalize()
    login = _NOT_LOGIN_CHARACTERS.sub("", sender_name)[:MAX_LOGIN_LENGTH]
    if not login:
        return (
            f"{name}: sorry, only maintainers can use me and you aren't one. Ask your admin "
            "to add your username to `maintainers` in `heimdall.yml`."
        )
    return (
        f"{name}: sorry @{login}, only maintainers can use me and you aren't one. Ask your "
        f"admin to add `{login}` to `maintainers` in `heimdall.yml`."
    )
