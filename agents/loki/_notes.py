"""The notes maintainers kept for Loki on a repo, read through ``recall`` and wrapped as data.

A note is maintainer-written, but it still reaches the model inside a ``pr_data`` block like any
tool result: the model may use it to decide what to review, never as a new rule.
"""

from collections.abc import Mapping
from typing import Protocol, cast

from agents._guardrails import wrap_pr_data
from agents.loki.config import AGENT_NAME
from heimdall.shared.logging import get_logger

MAX_NOTES_IN_PROMPT = 50
"""Most notes put in one review prompt; matches the per-repo note cap planned in HEI-95."""

NOTES_SOURCE = "recall"
"""``source`` attribute of the notes block, named after the tool that produced it."""

log = get_logger(__name__)


class NotesReader(Protocol):
    """The read-only memory tool Loki uses."""

    async def recall(self, installation_id: int, repo_id: int) -> dict[str, object]:
        """Return ``{"notes": [{"key", "value", ...}, ...]}`` for Loki on the repo."""
        ...


async def read_note_lines(
    tools: NotesReader, installation_id: int, context: Mapping[str, object]
) -> tuple[str, ...]:
    """Read Loki's notes for the PR's repo as ``key: value`` lines, or none if unavailable.

    Notes help a review but are never required, so a missing repo id or a failing tool gives
    an empty result instead of stopping the review.

    Args:
        tools: Loki's MCP client.
        installation_id: Tenant owning the PR.
        context: ``get_pr_context`` result; supplies ``repo_id``.

    Returns:
        At most ``MAX_NOTES_IN_PROMPT`` lines, in the order ``recall`` returned them.
    """
    repo_id = context.get("repo_id")
    if not isinstance(repo_id, int) or isinstance(repo_id, bool):
        return ()
    try:
        result = await tools.recall(installation_id, repo_id)
    except Exception as error:
        log.warning(
            "agent_notes_unavailable",
            agent=AGENT_NAME,
            installation_id=installation_id,
            repo_id=repo_id,
            error_type=type(error).__name__,
        )
        return ()
    return _note_lines(result.get("notes"))[:MAX_NOTES_IN_PROMPT]


def notes_block(lines: tuple[str, ...]) -> str:
    """Wrap note lines as one ``pr_data`` block, or return "" when there are none."""
    if not lines:
        return ""
    return wrap_pr_data(NOTES_SOURCE, "\n".join(lines))


def _note_lines(raw: object) -> tuple[str, ...]:
    """Keep the notes whose key and value are strings, as ``key: value`` lines."""
    if not isinstance(raw, list):
        return ()
    lines: list[str] = []
    for item in cast(list[object], raw):
        if not isinstance(item, dict):
            continue
        note = cast(dict[str, object], item)
        key, value = note.get("key"), note.get("value")
        if isinstance(key, str) and isinstance(value, str):
            lines.append(f"{key}: {value}")
    return tuple(lines)
