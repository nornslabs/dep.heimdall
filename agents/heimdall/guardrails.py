"""Heimdall's own guardrails: maintainers only, draft caps, and finding citation checks.

The runtime bounds turns, tokens and call counts. These rules enforce domain policy:
only a listed maintainer may trigger a command, at most 2 drafts can be saved per command,
drafts and cards must cite existing finding ids only, and the submitted result must name an
artifact this run saved.
"""

import re
from collections.abc import Mapping, Sequence
from typing import cast

from agents._submit import SubmitTool
from agents._toolbox import AgentTool
from agents.heimdall.config import MAX_DRAFTS
from agents.heimdall.schemas import SUBMIT_RESULT, DeskResult, desk_result_problems

_FINDING_ID_PATTERN = re.compile(
    r"\b(finding-[0-9a-zA-Z_-]+)\b"
    r"|(?:(?:finding|finding_id)\s*[:=#]?\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}))"
    r"|\[([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\]",
    re.IGNORECASE,
)


def find_quoted_finding_ids(text: str) -> tuple[str, ...]:
    """Extract quoted finding ids from prose or citation text.

    Args:
        text: Untrusted draft, card gap, or prompt text.

    Returns:
        All extracted finding identifiers.
    """
    matches = _FINDING_ID_PATTERN.findall(text)
    ids: list[str] = []
    for match in matches:
        for group in match:
            if group:
                ids.append(group)
    return tuple(ids)


class CommandGuard:
    """Refuse a command from a sender who is not in the repository's maintainer list."""

    def __init__(self, maintainers: tuple[str, ...]) -> None:
        """Keep the allowed maintainer logins for this run.

        Args:
            maintainers: GitHub logins from the repository's validated ``maintainers`` config.
        """
        self._maintainers = frozenset(login.casefold() for login in maintainers)

    def refusal(self, sender_login: str) -> str | None:
        """Say why this command must be refused, or None when the sender may issue it.

        Args:
            sender_login: GitHub login of the person who sent the command.

        Returns:
            One line naming the rule broken, or None. The comparison ignores case because
            Mattermost and GitHub can present the same login in different cases.
        """
        if sender_login.casefold() not in self._maintainers:
            return f"command refused: {sender_login!r} is not a maintainer for this repository"
        return None


class DeskGuard:
    """Guard draft limits and finding id citations for cards, drafts, and mechanical tasks."""

    def __init__(
        self,
        *,
        existing_finding_ids: Sequence[str] = (),
        max_drafts: int = MAX_DRAFTS,
    ) -> None:
        """Initialize with existing finding ids for this PR head and maximum draft count.

        Args:
            existing_finding_ids: Valid finding ids known for the current head commit.
            max_drafts: Maximum number of drafts permitted in one command run.
        """
        self._existing_finding_ids = frozenset(existing_finding_ids)
        self._max_drafts = max_drafts
        self._draft_count = 0

    @property
    def draft_count(self) -> int:
        """Number of drafts saved so far."""
        return self._draft_count

    def draft_refusal(self, arguments: Mapping[str, object]) -> str | None:
        """Refuse a save_draft call if over the draft cap or if citing an invented finding.

        Args:
            arguments: Arguments passed to ``save_draft``.

        Returns:
            Error message if refused, or None if allowed.
        """
        if self._draft_count >= self._max_drafts:
            return f"save_draft: at most {self._max_drafts} drafts per command"
        body = arguments.get("body")
        if isinstance(body, str):
            for fid in find_quoted_finding_ids(body):
                if fid not in self._existing_finding_ids:
                    return f"save_draft: finding id {fid!r} does not exist for this PR head"
        return None

    def record_draft(self) -> None:
        """Record one successfully saved draft."""
        self._draft_count += 1

    def card_refusal(self, arguments: Mapping[str, object]) -> str | None:
        """Refuse a save_card call if its gaps cite an invented finding id.

        Args:
            arguments: Arguments passed to ``save_card``.

        Returns:
            Error message if refused, or None if allowed.
        """
        gaps = arguments.get("gaps")
        if isinstance(gaps, list):
            for gap in cast(list[object], gaps):
                if isinstance(gap, str):
                    for fid in find_quoted_finding_ids(gap):
                        if fid not in self._existing_finding_ids:
                            return f"save_card: finding id {fid!r} does not exist for this PR head"
        return None

    def task_refusal(self, arguments: Mapping[str, object]) -> str | None:
        """Refuse a create_mechanical_task call if finding_id does not exist for this head.

        Args:
            arguments: Arguments passed to ``create_mechanical_task``.

        Returns:
            Error message if refused, or None if allowed.
        """
        fid = arguments.get("finding_id")
        if isinstance(fid, str) and fid not in self._existing_finding_ids:
            return f"create_mechanical_task: finding_id {fid!r} does not exist for this PR head"
        return None


def guard_save_draft(guard: DeskGuard, save_draft: AgentTool) -> AgentTool:
    """Wrap save_draft so draft count and finding citations are verified.

    Args:
        guard: The active DeskGuard instance.
        save_draft: The tool executing ``save_draft``.

    Returns:
        Guarded AgentTool.
    """

    async def handler(arguments: Mapping[str, object]) -> object:
        refusal = guard.draft_refusal(arguments)
        if refusal is not None:
            return f"error: {refusal}"
        result = await save_draft.handler(arguments)
        guard.record_draft()
        return result

    return AgentTool(save_draft.spec, handler)


def guard_save_card(guard: DeskGuard, save_card: AgentTool) -> AgentTool:
    """Wrap save_card so finding citations in gaps are verified.

    Args:
        guard: The active DeskGuard instance.
        save_card: The tool executing ``save_card``.

    Returns:
        Guarded AgentTool.
    """

    async def handler(arguments: Mapping[str, object]) -> object:
        refusal = guard.card_refusal(arguments)
        if refusal is not None:
            return f"error: {refusal}"
        return await save_card.handler(arguments)

    return AgentTool(save_card.spec, handler)


def guard_create_mechanical_task(guard: DeskGuard, create_task: AgentTool) -> AgentTool:
    """Wrap create_mechanical_task so finding_id is verified against existing findings.

    Args:
        guard: The active DeskGuard instance.
        create_task: The tool executing ``create_mechanical_task``.

    Returns:
        Guarded AgentTool.
    """

    async def handler(arguments: Mapping[str, object]) -> object:
        refusal = guard.task_refusal(arguments)
        if refusal is not None:
            return f"error: {refusal}"
        return await create_task.handler(arguments)

    return AgentTool(create_task.spec, handler)


class SavedIdTracker:
    """Track artifact ids that Heimdall's tools returned during a run."""

    def __init__(self) -> None:
        """Start with an empty record."""
        self._ids: list[str] = []

    def record(self, artifact_id: str) -> None:
        """Remember one id returned by ``save_card``, ``save_draft`` or ``create_mechanical_task``.

        Args:
            artifact_id: The id the MCP tool returned.
        """
        self._ids.append(artifact_id)

    def saved_ids(self) -> tuple[str, ...]:
        """Return every id saved during this run, in the order they were returned."""
        return tuple(self._ids)


def submit_desk_result_tool(
    tracker: SavedIdTracker, name: str = SUBMIT_RESULT.name
) -> SubmitTool[DeskResult]:
    """Build Heimdall's submit tool with the saved-id cross-check bound to this run.

    Args:
        tracker: The run's id tracker, updated by the tool wrappers as saves succeed.
        name: Name of the submit tool, defaulting to SUBMIT_RESULT.name.

    Returns:
        The tool whose cross-check turns a result that names an unsaved id into the model's
        one repair round.
    """

    async def cross_check(result: DeskResult) -> tuple[str, ...]:
        return desk_result_problems(result, tracker.saved_ids())

    return SubmitTool(
        name,
        DeskResult,
        SUBMIT_RESULT.spec.description,
        cross_check=cross_check,
    )
