"""Recognise a maintainer's "are you there?" message to an agent and word the answer.

Message bodies are untrusted, so recognition is an exact match on a few words after the
leading @mentions, with a length cap. Nothing from the body is ever copied into a reply.
"""

import re
from enum import Enum

from heimdall.shared.agents import AgentName

MAX_PROMPT_LENGTH = 200
_LEADING_MENTIONS = re.compile(
    "^\\s*(?:@(?:" + "|".join(name.value for name in AgentName) + ")\\b[:,]?\\s*)+",
    re.IGNORECASE,
)
_TRAILING_PUNCTUATION = " \t\r\n!?.:"
_PING_WORDS = frozenset({"ping", "alive", "status"})
_HELLO_WORDS = frozenset({"hello", "hi", "hey"})
_ROLES = {
    AgentName.HUGINN: "reproduce a fix by running the tests with and without it",
    AgentName.LOKI: "review a pull request's diff and file cited findings",
    AgentName.HEIMDALL: "put the review and the repro result on one approval card",
}


class PresencePrompt(Enum):
    """The kinds of presence prompt an agent answers."""

    PING = "ping"
    HELLO = "hello"


def parse_presence_prompt(body: str) -> PresencePrompt | None:
    """Decide whether a message is a bare ping or greeting, ignoring leading agent mentions.

    Args:
        body: Untrusted message text, including the @mention that addressed the agent.

    Returns:
        The kind of prompt, or None for anything else: extra words, other mentions, shell
        syntax, instructions, empty text or text longer than ``MAX_PROMPT_LENGTH``.
    """
    if len(body) > MAX_PROMPT_LENGTH:
        return None
    rest = _LEADING_MENTIONS.sub("", body, count=1)
    word = rest.strip().rstrip(_TRAILING_PUNCTUATION).casefold()
    if word in _PING_WORDS:
        return PresencePrompt.PING
    if word in _HELLO_WORDS:
        return PresencePrompt.HELLO
    return None


def format_presence_reply(agent: AgentName, prompt: PresencePrompt) -> str:
    """Word the one-line answer that says the agent is running.

    Args:
        agent: The agent answering.
        prompt: What it was asked.

    Returns:
        Fixed text naming the agent; it never contains the asker's words.
    """
    name = agent.value.capitalize()
    if prompt is PresencePrompt.PING:
        return f"{name}: pong, I'm alive."
    return f"{name}: hello! I'm alive. I {_ROLES[agent]}."
