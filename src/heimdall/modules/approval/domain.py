"""Pure approval authorization and hash rules."""

from dataclasses import dataclass
from uuid import UUID

from heimdall.shared.errors import DraftChanged, NotAMaintainer


@dataclass(frozen=True)
class ApprovalRef:
    """Recorded approval crossing the approval module boundary."""

    id: UUID
    draft_id: UUID
    approver: str
    draft_hash: str
    via: str


@dataclass(frozen=True)
class ApprovedDraft:
    """Exact text authorized for a GitHub write by one matching approval."""

    draft_id: UUID
    pr_id: UUID
    body: str
    body_hash: str
    approver: str
    via: str
    kind: str = "comment"
    head_sha: str | None = None


def validate_approval(
    approver: str,
    maintainers: tuple[str, ...],
    expected_hash: str,
    submitted_hash: str,
    *,
    superseded: bool,
) -> None:
    """Require a configured maintainer and an unchanged, active draft.

    Args:
        approver: GitHub login reacting to the draft.
        maintainers: Maintainer logins configured for this repository.
        expected_hash: Hash stored with the current draft text.
        submitted_hash: Hash included in the approval action.
        superseded: Whether a newer draft replaced this one.

    Raises:
        NotAMaintainer: The login is not configured as a repository maintainer.
        DraftChanged: The hash differs or the draft has been superseded.
    """
    allowed = {login.casefold() for login in maintainers}
    if approver.casefold() not in allowed:
        raise NotAMaintainer(f"{approver} is not a configured maintainer")
    if superseded or submitted_hash != expected_hash:
        raise DraftChanged("draft changed or was superseded before approval")


def validate_posting_hash(
    approved_hash: str,
    queued_hash: str,
    stored_hash: str,
    body_hash: str,
    *,
    superseded: bool,
) -> None:
    """Require the outbox, approval row, and exact draft body to share one hash.

    Args:
        approved_hash: Hash stored with the approval row.
        queued_hash: Hash captured by the outbox payload.
        stored_hash: Hash saved with the draft row.
        body_hash: Hash recalculated from the current body.
        superseded: Whether a newer draft replaced this draft.

    Raises:
        DraftChanged: Any hash differs or the draft was superseded.
    """
    hashes = {approved_hash, queued_hash, stored_hash, body_hash}
    if superseded or len(hashes) != 1:
        raise DraftChanged("approval does not match the current exact draft text")


__all__ = ["ApprovalRef", "ApprovedDraft", "validate_approval", "validate_posting_hash"]
