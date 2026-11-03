"""Pure desk cards, draft hashes, and maintainer command parsing."""

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class CommandVerb(StrEnum):
    """Commands recognized in a Switch room."""

    APPROVE = "approve"
    REVISE = "revise"
    FIX = "fix"
    RETRY = "retry"


@dataclass(frozen=True)
class DeskCommand:
    """Validated command token and its optional target or text."""

    verb: CommandVerb
    target_id: UUID | None = None
    text: str | None = None


@dataclass(frozen=True)
class CardContent:
    """Review information rendered into a Switch PR card."""

    number: int
    title: str
    head_sha: str
    intake_summary: str
    repro_summary: str
    findings: tuple[str, ...]


@dataclass(frozen=True)
class CardRef:
    """Card identity and rendered body crossing the desk module border."""

    id: UUID
    pr_id: UUID
    head_sha: str
    body: str


@dataclass(frozen=True)
class DraftRef:
    """Draft identity, exact body, and the hash requiring approval."""

    id: UUID
    pr_id: UUID
    kind: str
    body: str
    body_hash: str
    superseded_by: UUID | None = None


def parse_command(message: str) -> DeskCommand | None:
    """Parse a supported slash command without interpreting its text as a shell command.

    Args:
        message: Untrusted room message.

    Returns:
        Parsed command, or None when it is not a valid Heimdall command.
    """
    parts = message.strip().split(maxsplit=3)
    if len(parts) < 2 or parts[0].casefold() != "/heimdall":
        return None
    try:
        verb = CommandVerb(parts[1].casefold())
    except ValueError:
        return None
    if verb is CommandVerb.RETRY:
        return DeskCommand(verb) if len(parts) == 2 else None
    if len(parts) < 3:
        return None
    try:
        target_id = UUID(parts[2])
    except ValueError:
        return None
    if verb is CommandVerb.REVISE:
        if len(parts) != 4 or not parts[3].strip():
            return None
        return DeskCommand(verb, target_id, parts[3].strip())
    if len(parts) != 3:
        return None
    return DeskCommand(verb, target_id)


def render_card(content: CardContent) -> str:
    """Render a PR summary card without treating submitted text as instructions.

    Args:
        content: PR, intake, reproduction and review values to display.

    Returns:
        Markdown card body for a Switch thread.
    """
    lines = [
        f"## PR #{content.number}: {_escape(content.title)}",
        f"Head: `{content.head_sha}`",
        "",
        "### Intake",
        _escape(content.intake_summary),
        "",
        "### Reproduction",
        _escape(content.repro_summary),
        "",
        "### Findings",
    ]
    lines.extend(f"- {_escape(finding)}" for finding in content.findings)
    if not content.findings:
        lines.append("- No findings.")
    return "\n".join(lines)


def hash_draft(body: str) -> str:
    """Hash the exact draft bytes the maintainer will approve.

    Args:
        body: Exact text to be posted after approval.

    Returns:
        Lowercase SHA-256 hex digest.
    """
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _escape(value: str) -> str:
    """Escape user supplied Markdown punctuation for safe display."""
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", value)


__all__ = [
    "CardContent",
    "CardRef",
    "CommandVerb",
    "DeskCommand",
    "DraftRef",
    "hash_draft",
    "parse_command",
    "render_card",
]
