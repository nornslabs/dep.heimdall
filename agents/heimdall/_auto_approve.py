"""Let a maintainer's own message approve the drafts that are safe to act on at once.

Closing, reopening and asking the contributor for something take effect from the maintainer's
message alone: the sender is already a verified maintainer and a tool-less check has confirmed
the message asks to act. A merge never does; it waits for its own explicit approval of the
exact merge text. Approval still goes through ``record_approval``, so the maintainer list, the
draft hash and supersession are checked exactly as they are for a 👍.
"""

import uuid
from collections.abc import Mapping
from typing import Final

from agents._mention_ports import ActSetup
from agents._toolbox import AgentTool
from heimdall.modules.approval.api import record_approval
from heimdall.shared.errors import DraftChanged, NotAMaintainer
from heimdall.shared.github import RepoRef
from heimdall.shared.logging import get_logger

APPROVAL_VIA: Final = "message"
APPROVED_ON_SAVE: Final = frozenset({"draft_close", "draft_reopen", "draft_ask_contributor"})
QUEUED_STATUS: Final = "approved by the maintainer's message and queued for GitHub"
WAITING_STATUS: Final = (
    "saved, not posted: a maintainer must approve this exact text with a thumbs-up "
    "or by replying that they approve"
)
REFUSED_STATUS: Final = "saved, not approved: {reason}"

log = get_logger(__name__)


def approve_on_save(setup: ActSetup, tool: AgentTool) -> AgentTool:
    """Wrap a draft tool so the saved draft is approved when its kind allows it.

    Args:
        setup: The job's database, config reader, tenant, PR and the maintainer who asked.
        tool: A draft tool whose result carries ``draft_id`` and ``body_hash``.

    Returns:
        The tool, whose result gains a ``status`` line saying whether the draft was approved,
        so the model reports what actually happened. A refused save passes through unchanged.
    """

    async def handler(arguments: Mapping[str, object]) -> object:
        result = await tool.handler(arguments)
        saved = _saved_draft(result)
        if saved is None:
            return result
        draft_id, body_hash = saved
        status = await _status(setup, tool.spec.name, draft_id, body_hash)
        return {"draft_id": str(draft_id), "body_hash": body_hash, "status": status}

    return AgentTool(tool.spec, handler)


def _saved_draft(result: object) -> tuple[uuid.UUID, str] | None:
    """Read the draft id and hash from a successful save, or None for a refusal."""
    if not isinstance(result, dict):
        return None
    fields: dict[str, object] = dict(result)  # pyright: ignore[reportUnknownArgumentType]
    draft_id = fields.get("draft_id")
    body_hash = fields.get("body_hash")
    if not isinstance(draft_id, str) or not isinstance(body_hash, str):
        return None
    try:
        return uuid.UUID(draft_id), body_hash
    except ValueError:
        return None


async def _status(setup: ActSetup, tool_name: str, draft_id: uuid.UUID, body_hash: str) -> str:
    """Approve the draft when its kind allows it and say what happened, for the model."""
    if tool_name not in APPROVED_ON_SAVE:
        return WAITING_STATUS
    if not setup.sender:
        return REFUSED_STATUS.format(reason="the sender is unknown")
    repo = RepoRef(setup.ctx.installation_id, setup.pr.repo_full_name)
    try:
        await record_approval(
            setup.sessions,
            setup.reader,
            setup.ctx,
            repo,
            draft_id,
            setup.sender,
            body_hash,
            via=APPROVAL_VIA,
        )
    except NotAMaintainer:
        return REFUSED_STATUS.format(reason="the sender is not a maintainer")
    except DraftChanged:
        return REFUSED_STATUS.format(reason="the draft changed")
    log.info("draft_approved_on_save", tool=tool_name, draft_id=str(draft_id))
    return QUEUED_STATUS
