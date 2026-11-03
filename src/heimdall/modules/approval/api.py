"""Public approval interface; other modules import only from this file."""

from heimdall.modules.approval.domain import (
    ApprovalRef,
    ApprovedDraft,
    validate_approval,
    validate_posting_hash,
)
from heimdall.modules.approval.service import (
    approve_by_policy,
    get_approved_draft,
    pause_auto_suggestions,
    queue_repro_workflow_dispatch,
    record_approval,
    verify_repro_workflow_dispatch,
)

__all__ = [
    "ApprovalRef",
    "ApprovedDraft",
    "approve_by_policy",
    "get_approved_draft",
    "pause_auto_suggestions",
    "queue_repro_workflow_dispatch",
    "record_approval",
    "validate_approval",
    "validate_posting_hash",
    "verify_repro_workflow_dispatch",
]
