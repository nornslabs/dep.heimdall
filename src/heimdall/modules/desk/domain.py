"""Pure desk cards, draft hashes, and maintainer command parsing."""

import hashlib
import html
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

PR_CLOSE_KIND: Final = "pr_close"
PR_REOPEN_KIND: Final = "pr_reopen"
PR_MERGE_KIND: Final = "pr_merge"
PR_ASK_KIND: Final = "pr_ask"
PR_ACTION_KINDS: Final = frozenset({PR_CLOSE_KIND, PR_REOPEN_KIND, PR_MERGE_KIND})
"""Draft kinds whose approval changes the pull request itself, not just comments on it.

``PR_ASK_KIND`` is only a comment asking the contributor for something, so it is not listed.
"""


class CommandVerb(StrEnum):
    """Commands recognized in a Switch room."""

    APPROVE = "approve"
    REVISE = "revise"
    FIX = "fix"
    RETRY = "retry"
    PAUSE_AUTO = "pause_auto"


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
    head_sha: str | None = None


@dataclass(frozen=True)
class MechanicalTaskRef:
    """Persisted task and the draft metadata required for approval."""

    id: str
    pr_id: UUID
    finding_id: UUID
    draft_id: UUID
    category: str
    suggestion: str
    task_block: str
    body_hash: str


def parse_command(message: str) -> DeskCommand | None:
    """Parse a supported slash command without interpreting its text as a shell command.

    Args:
        message: Untrusted room message.

    Returns:
        Parsed command, or None when it is not a valid Heimdall command.
    """
    parts = message.strip().split(maxsplit=3)
    if (
        len(parts) == 3
        and parts[0].casefold() in {"@heimdall", "/heimdall"}
        and parts[1].casefold() == "pause"
        and parts[2].casefold() == "auto"
    ):
        return DeskCommand(CommandVerb.PAUSE_AUTO)
    if len(parts) < 2 or parts[0].casefold() != "/heimdall":
        return None
    try:
        verb = CommandVerb(parts[1].casefold())
    except ValueError:
        return None
    if verb in {CommandVerb.RETRY, CommandVerb.APPROVE} and len(parts) == 2:
        return DeskCommand(verb)
    if verb is CommandVerb.RETRY:
        return None
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
        f"**PR #{content.number}: {_escape(content.title)}** (`{content.head_sha[:7]}`)",
        "",
        _escape(content.intake_summary),
        "",
        _escape(content.repro_summary),
    ]
    if content.findings:
        lines.extend(["", "What caught my eye:"])
        lines.extend(f"- {_escape(finding)}" for finding in content.findings)
    return "\n".join(lines)


def hash_draft(body: str) -> str:
    """Hash the exact draft bytes the maintainer will approve.

    Args:
        body: Exact text to be posted after approval.

    Returns:
        Lowercase SHA-256 hex digest.
    """
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def format_approval_message(
    body: str,
    draft_id: UUID,
    body_hash: str,
    installation_id: int,
    repo_full_name: str,
) -> str:
    """Append hidden approval metadata while keeping the saved draft body unchanged.

    Args:
        body: Exact saved draft body whose hash is approved.
        draft_id: Identity returned by the desk module.
        body_hash: Hash of the exact saved draft body.
        installation_id: Tenant owning the draft.
        repo_full_name: Repository that owns the draft's pull request.

    Returns:
        A Switch post body with a hidden marker after the unchanged draft text.
    """
    # A Markdown link-reference definition renders as nothing, unlike an HTML comment, which
    # Mattermost shows as text.
    marker = (
        f"[//]: # (heimdall-approval draft_id={draft_id} draft_hash={body_hash} "
        f"installation_id={installation_id} repo={repo_full_name})"
    )
    return f"{body.rstrip()}\n\n{marker}"


_PLAIN_LOGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")


def mention_maintainers(message: str, logins: tuple[str, ...]) -> str:
    """Put an ``@`` mention of each maintainer above a card so they are notified of it.

    The mention is only for the room: the saved draft and its hash are untouched, so what a
    maintainer approves and what reaches GitHub stay exactly the card body.

    Args:
        message: The Switch post body, including its hidden approval marker.
        logins: Maintainer logins. Any that is not a plain login is skipped, so repository
            config cannot inject a broadcast mention or markup into the room.

    Returns:
        The message with a mention line above it, or unchanged when no login is usable.
    """
    mentions = [f"@{login}" for login in logins if _PLAIN_LOGIN.fullmatch(login)]
    if not mentions:
        return message
    return f"{' '.join(mentions)}, your review is needed:\n\n{message}"


def render_mechanical_task_block(
    task_id: str,
    finding_id: UUID,
    file: str,
    line: int,
    category: str,
    suggestion: str,
) -> str:
    """Render a hidden task marker and a readable suggestion without trusting finding text.

    Args:
        task_id: Stable task identifier assigned by the desk service.
        finding_id: Review finding this task addresses.
        file: Repository path cited by the finding.
        line: One-based line cited by the finding.
        category: Mechanical category from the saved finding.
        suggestion: Finding text supplied by the review agent.

    Returns:
        Markdown suggestion followed by the machine-readable task marker.
    """
    safe_suggestion = html.escape(" ".join(suggestion.split()), quote=False)
    safe_file = html.escape(" ".join(file.split()), quote=True).replace("--", "- -")
    safe_category = html.escape(category, quote=True)
    marker = (
        f'<!-- heimdall-task id="{task_id}" finding="{finding_id}" '
        f'file="{safe_file}" line="{line}" category="{safe_category}" -->'
    )
    return f"Suggestion: {safe_suggestion}\n\n{marker}"


def _escape(value: str) -> str:
    """Escape user supplied Markdown punctuation for safe display."""
    # Only characters that can open links, emphasis, headings, quotes or tables. Escaping "." and
    # "-" as well made ordinary prose unreadable ("README\.md").
    return re.sub(r"([\\`*_{}\[\]#|>~])", r"\\\1", value)


__all__ = [
    "PR_ACTION_KINDS",
    "PR_ASK_KIND",
    "PR_CLOSE_KIND",
    "PR_MERGE_KIND",
    "PR_REOPEN_KIND",
    "CardContent",
    "CardRef",
    "CommandVerb",
    "DeskCommand",
    "DraftRef",
    "MechanicalTaskRef",
    "format_approval_message",
    "hash_draft",
    "parse_command",
    "render_card",
    "render_mechanical_task_block",
]
