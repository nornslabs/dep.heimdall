"""The brief Heimdall starts from: PR metadata, then repro status, then findings.

The order lets the model focus on the card or command without reading the diff or any source
files. Only stored results go in the brief; raw PR text is wrapped as PR data so the model
treats it as untrusted content.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from agents._guardrails import wrap_pr_data
from heimdall.adapters.ai import ChatMessage

PROMPT_PATH = Path(__file__).with_name("prompt.md")


@dataclass(frozen=True)
class DeskBrief:
    """What Heimdall knows before its first tool call.

    Attributes:
        installation_id: Tenant whose tools Heimdall may call.
        pr_id: Triage identifier every scoped tool takes.
        repo_full_name: ``owner/name`` of the repository under review.
        number: Pull request number.
        title: PR title; untrusted text from GitHub.
        head_sha: Head commit the card or draft must match.
        repro_summary: Rendered repro status text, or an empty string when no run exists.
        finding_summaries: One short summary per accepted finding, in finding order.
        command_text: The maintainer's command text, or an empty string for card wake-ups.
    """

    installation_id: int
    pr_id: str
    repo_full_name: str
    number: int
    title: str
    head_sha: str
    repro_summary: str
    finding_summaries: tuple[str, ...]
    command_text: str


def build_desk_brief(
    *,
    installation_id: int,
    pr_id: str,
    repo_full_name: str,
    number: int,
    title: str,
    head_sha: str,
    repro_summary: str = "",
    finding_summaries: tuple[str, ...] = (),
    command_text: str = "",
) -> DeskBrief:
    """Assemble the brief Heimdall receives at the start of a job.

    Args:
        installation_id: Tenant whose tools Heimdall may call.
        pr_id: Triage identifier every scoped tool takes.
        repo_full_name: ``owner/name`` of the repository.
        number: Pull request number.
        title: PR title, treated as untrusted text.
        head_sha: Head commit this card or command is about.
        repro_summary: Pre-fetched repro result rendered to text, or ``""`` when none exists.
        finding_summaries: Pre-fetched finding summaries in finding order, or ``()`` when none.
        command_text: The maintainer's raw command, or ``""`` for ``pr.ready_for_card`` wake-ups.

    Returns:
        The desk brief.
    """
    return DeskBrief(
        installation_id=installation_id,
        pr_id=pr_id,
        repo_full_name=repo_full_name,
        number=number,
        title=title,
        head_sha=head_sha,
        repro_summary=repro_summary,
        finding_summaries=finding_summaries,
        command_text=command_text,
    )


def build_messages(
    brief: DeskBrief,
    instructions: str,
    skill_index: str,
) -> tuple[ChatMessage, ...]:
    """Build the system prompt and the single user turn that starts a Heimdall run.

    Args:
        brief: The PR, its repro status and its findings.
        instructions: Heimdall's role prompt from ``prompt.md``.
        skill_index: The ``name: description`` list of skills, or an empty string.

    Returns:
        One system message and one user message holding the brief as PR data.
    """
    system = f"{instructions}\n\n{skill_index}" if skill_index else instructions
    return (ChatMessage("system", system), ChatMessage("user", _user_turn(brief)))


def _user_turn(brief: DeskBrief) -> str:
    """Render the tool scope, then the brief, with PR text wrapped as data."""
    parts = [_brief_block(brief)]
    if brief.command_text:
        parts.append(_command_block(brief.command_text))
    return "\n\n".join(parts)


def _brief_block(brief: DeskBrief) -> str:
    """Render the brief as PR data, with the tool scope and task stated first."""
    payload = {
        "repository": brief.repo_full_name,
        "pull_request": {
            "number": brief.number,
            "title": brief.title,
            "head_sha": brief.head_sha,
        },
        "repro_status": brief.repro_summary or "No reproduction run is available.",
        "findings": list(brief.finding_summaries),
    }
    scope = f"installation_id {brief.installation_id} and pr_id {brief.pr_id}"
    plan = (
        f"Your tools are scoped to {scope}.\n"
        "Load a skill whose description matches your task, then use the saved results in the\n"
        "brief. Do not read the diff or any source files. Save the card, draft or task with\n"
        "the appropriate tool, then call submit_result."
    )
    return f"{plan}\n\n{wrap_pr_data('pr_brief', json.dumps(payload, indent=2))}"


def _command_block(command_text: str) -> str:
    """Wrap the maintainer's command as PR data so it is treated as untrusted input."""
    return wrap_pr_data("maintainer_command", command_text)
