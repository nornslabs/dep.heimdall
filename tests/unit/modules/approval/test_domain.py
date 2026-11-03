"""Pure approval authorization checks."""

import pytest

from heimdall.modules.approval.api import validate_approval, validate_posting_hash
from heimdall.shared.errors import DraftChanged, NotAMaintainer


def test_validate_approval_accepts_case_insensitive_maintainer_and_exact_hash() -> None:
    """Match GitHub login case-insensitively and retain exact draft hash checks."""
    validate_approval("Reviewer", ("reviewer",), "abc", "abc", superseded=False)


def test_validate_approval_rejects_non_maintainer() -> None:
    """Reject an approval from a login absent from repository config."""
    with pytest.raises(NotAMaintainer):
        validate_approval("stranger", ("reviewer",), "abc", "abc", superseded=False)


def test_validate_approval_rejects_stale_or_superseded_drafts() -> None:
    """Reject changed text and drafts replaced by a newer version."""
    with pytest.raises(DraftChanged):
        validate_approval("reviewer", ("reviewer",), "abc", "changed", superseded=False)
    with pytest.raises(DraftChanged):
        validate_approval("reviewer", ("reviewer",), "abc", "abc", superseded=True)


def test_validate_posting_hash_requires_every_copy_to_match() -> None:
    """Allow delivery only when approval, outbox, draft, and body hashes match."""
    validate_posting_hash("abc", "abc", "abc", "abc", superseded=False)

    with pytest.raises(DraftChanged):
        validate_posting_hash("abc", "changed", "abc", "abc", superseded=False)
    with pytest.raises(DraftChanged):
        validate_posting_hash("abc", "abc", "abc", "abc", superseded=True)
