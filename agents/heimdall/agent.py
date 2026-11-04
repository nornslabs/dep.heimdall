"""Heimdall's review-card workflow and maintainer-command routing."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from heimdall.modules.desk.api import (
    CardContent,
    DeskCommand,
    format_approval_message,
    parse_command,
    render_card,
)
from heimdall.modules.repro.api import ReproStatus, Verdict


class HeimdallTools(Protocol):
    """MCP calls available to Heimdall's card workflow."""

    async def list_findings(self, installation_id: int, pr_id: str) -> list[dict[str, object]]:
        """Load accepted findings for the current PR head."""
        ...

    async def get_repro(self, installation_id: int, run_id: str) -> dict[str, object]:
        """Load the latest paired reproduction result."""
        ...

    async def save_card(
        self,
        installation_id: int,
        pr_id: str,
        number: int,
        title: str,
        head_sha: str,
        rules_summary: str,
        fix_status: str,
        gaps: list[str],
        suggested_action: str,
    ) -> dict[str, str]:
        """Save a Switch card for later approval and delivery."""
        ...

    async def save_draft(
        self,
        installation_id: int,
        pr_id: str,
        head_sha: str,
        kind: str,
        body: str,
    ) -> dict[str, str]:
        """Save exact card text and its approval hash."""
        ...


class CommandHandlers(Protocol):
    """Trusted callbacks that execute a parsed maintainer command."""

    async def approve(self, command: DeskCommand) -> str:
        """Record approval of the targeted exact draft."""
        ...

    async def revise(self, command: DeskCommand) -> str:
        """Create a replacement draft from maintainer-provided text."""
        ...

    async def fix(self, command: DeskCommand) -> str:
        """Start the configured mechanical fix workflow for a finding."""
        ...

    async def retry(self, command: DeskCommand) -> str:
        """Retry the current PR's reproduction attempt."""
        ...

    async def pause_auto(self, command: DeskCommand) -> str:
        """Pause automatic mechanical suggestions for the current repository."""
        ...


@dataclass(frozen=True)
class CardResult:
    """Saved Switch card and approval-bound draft identifiers."""

    card_id: UUID
    draft_id: UUID
    body_hash: str
    fix_status: str
    approval_message: str


@dataclass(frozen=True)
class CommandResult:
    """Parsed command and response from its trusted handler."""

    command: DeskCommand
    response: str


async def build_review_card(
    tools: HeimdallTools,
    installation_id: int,
    pr_id: UUID,
    number: int,
    repo_full_name: str,
    title: str,
    head_sha: str,
    rules_summary: str,
    suggested_action: str,
    repro_run_id: UUID | None,
) -> CardResult:
    """Collect current findings and reproduction status, then save card and draft.

    Args:
        tools: MCP client restricted to Heimdall's tools.
        installation_id: Tenant owning the pull request.
        pr_id: Triage PR row identifier.
        number: GitHub pull request number.
        repo_full_name: Repository name shown in the approval metadata.
        title: PR title treated as untrusted display data.
        head_sha: Head commit the card must match.
        rules_summary: Intake result summary from trusted stored rule outcomes.
        suggested_action: Maintainer-facing next step.
        repro_run_id: Latest reproduction run, when one exists.

    Returns:
        Persisted card and its approval-bound draft identity.
    """
    findings = await tools.list_findings(installation_id, str(pr_id))
    gaps = [_finding_summary(item) for item in findings]
    fix_status = "No reproduction run is available."
    if repro_run_id is not None:
        result = await tools.get_repro(installation_id, str(repro_run_id))
        fix_status = _repro_summary(result)
    card = await tools.save_card(
        installation_id,
        str(pr_id),
        number,
        title,
        head_sha,
        rules_summary,
        fix_status,
        gaps,
        suggested_action,
    )
    draft_body = render_card(
        CardContent(
            number,
            title,
            head_sha,
            rules_summary,
            fix_status,
            (*gaps, f"Suggested action: {suggested_action}"),
        )
    )
    draft = await tools.save_draft(
        installation_id,
        str(pr_id),
        head_sha,
        "switch_card",
        draft_body,
    )
    return CardResult(
        UUID(card["card_id"]),
        UUID(draft["draft_id"]),
        draft["body_hash"],
        fix_status,
        format_approval_message(
            draft_body,
            UUID(draft["draft_id"]),
            draft["body_hash"],
            installation_id,
            repo_full_name,
        ),
    )


async def handle_command(message: str, handlers: CommandHandlers) -> CommandResult | None:
    """Parse a maintainer message and dispatch only supported commands.

    Args:
        message: Untrusted Switch room message.
        handlers: Trusted application callbacks for each command action.

    Returns:
        Command result, or None when the message is not a valid Heimdall command.
    """
    command = parse_command(message)
    if command is None:
        return None
    action = {
        "approve": handlers.approve,
        "revise": handlers.revise,
        "fix": handlers.fix,
        "retry": handlers.retry,
        "pause_auto": handlers.pause_auto,
    }[command.verb.value]
    return CommandResult(command, await action(command))


def _finding_summary(finding: dict[str, object]) -> str:
    """Render one finding citation for the review card."""
    file = finding.get("file")
    line = finding.get("line")
    text = finding.get("text")
    if not isinstance(file, str) or not isinstance(line, int) or not isinstance(text, str):
        raise ValueError("finding result has invalid citation data")
    return f"{file}:{line} — {text}"


def _repro_summary(result: dict[str, object]) -> str:
    """Summarize persisted reproduction status without overstating certainty."""
    try:
        status = ReproStatus(str(result["status"]))
        verdict_value = result.get("verdict")
        verdict = Verdict(str(verdict_value)) if verdict_value is not None else None
    except (KeyError, ValueError) as error:
        raise ValueError("reproduction result is malformed") from error
    if status is ReproStatus.FINISHED and verdict is not None:
        return {
            Verdict.FIX_CONFIRMED: (
                "Fix confirmed: tests passed with the fix and failed without it."
            ),
            Verdict.FIX_FAILED: "Fix failed: tests did not pass with the proposed fix.",
            Verdict.NOT_REPRODUCED: "Issue not reproduced: tests passed with and without the fix.",
        }[verdict]
    return {
        ReproStatus.TIMED_OUT: "Reproduction timed out; no verdict is available.",
        ReproStatus.STALE: "Reproduction is stale because the PR head changed.",
    }.get(status, "Reproduction is still running; no verdict is available.")
