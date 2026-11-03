import pytest

from heimdall.modules.audit.api import MAX_FIELD_LENGTH, MAX_PAYLOAD_BYTES, AuditEntry, pr_subject
from heimdall.shared.context import PrRef
from heimdall.shared.ids import RepoId


def test_pr_subject_names_the_repo_and_pr_number() -> None:
    assert pr_subject(PrRef(RepoId(42), 7)) == "pr:42#7"


def test_audit_entry_keeps_scalar_payload_values() -> None:
    entry = AuditEntry(
        actor="reviewer",
        action="draft.approved",
        subject="pr:42#7",
        payload={"draft_id": "d-1", "pr_number": 7, "auto": False, "note": None},
    )

    assert entry.payload == {"draft_id": "d-1", "pr_number": 7, "auto": False, "note": None}


def test_audit_entry_payload_defaults_to_empty() -> None:
    assert AuditEntry("policy:auto_suggest", "draft.approved", "pr:1#1").payload == {}


@pytest.mark.parametrize(
    ("actor", "action", "subject"),
    [
        ("", "draft.approved", "pr:1#1"),
        ("reviewer", "  ", "pr:1#1"),
        ("reviewer", "draft.approved", ""),
        ("r" * (MAX_FIELD_LENGTH + 1), "draft.approved", "pr:1#1"),
        ("reviewer", "a" * (MAX_FIELD_LENGTH + 1), "pr:1#1"),
        ("reviewer", "draft.approved", "s" * (MAX_FIELD_LENGTH + 1)),
    ],
)
def test_audit_entry_rejects_blank_or_overlong_fields(
    actor: str, action: str, subject: str
) -> None:
    with pytest.raises(ValueError):
        AuditEntry(actor, action, subject)


def test_audit_entry_rejects_payload_over_the_size_cap() -> None:
    with pytest.raises(ValueError, match="payload"):
        AuditEntry("reviewer", "draft.approved", "pr:1#1", {"body": "x" * MAX_PAYLOAD_BYTES})


def test_audit_entry_rejects_blank_payload_keys() -> None:
    with pytest.raises(ValueError, match="payload"):
        AuditEntry("reviewer", "draft.approved", "pr:1#1", {" ": "value"})
