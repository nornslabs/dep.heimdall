"""Helpers for messages addressed to an agent: stripping @mentions and refusing non-maintainers.

Message bodies are untrusted. The one thing a reply here echoes is the sender's name, and only
after it is cut down to the characters a GitHub login can contain.
"""

import re
from datetime import UTC, datetime

from heimdall.shared.agents import AgentName

MAX_LOGIN_LENGTH = 39
_NOT_LOGIN_CHARACTERS = re.compile(r"[^A-Za-z0-9._-]")
_LEADING_MENTIONS = re.compile(
    "^\\s*(?:@(?:" + "|".join(name.value for name in AgentName) + ")\\b[:,]?\\s*)+",
    re.IGNORECASE,
)


def strip_leading_mentions(body: str) -> str:
    """Drop the run of agent @mentions that opens a message, and surrounding whitespace.

    Args:
        body: Untrusted message text.

    Returns:
        What the sender asked, without the mentions that addressed the agents.
    """
    return _LEADING_MENTIONS.sub("", body, count=1).strip()


def format_rate_limited_reply(agent: AgentName, retry_after: datetime) -> str:
    """Word the one notice a sender gets when they have asked an agent too many questions.

    Args:
        agent: The agent answering.
        retry_after: When the sender's allowance comes back; shown in UTC.

    Returns:
        Fixed text naming the agent and the time to try again. It mentions nobody and repeats
        nothing from the sender's message.
    """
    when = retry_after.astimezone(UTC).strftime("%H:%M")
    return (
        f"{agent.value.capitalize()}: you're rate-limited for now, so I'll stay quiet; "
        f"try again after {when} UTC."
    )


def format_not_maintainer_reply(agent: AgentName, sender_name: str) -> str:
    """Word the answer to someone who addressed an agent but is not on the maintainer list.

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
