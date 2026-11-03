"""Persist exact authorization for GitHub Actions workflow dispatches."""

import uuid
from datetime import datetime

from sqlmodel import Field

from heimdall.adapters.db.columns import set_on_insert, uuid_pk
from heimdall.adapters.db.tenant import TenantScoped


class WorkflowDispatchApproval(TenantScoped, table=True):
    """One policy authorization for a specific reproduction dispatch request."""

    __tablename__ = "workflow_dispatch_approvals"  # pyright: ignore[reportAssignmentType]

    id: uuid.UUID = uuid_pk()
    repro_run_id: uuid.UUID = Field(foreign_key="repro_runs.id", unique=True)
    request_hash: str
    approver: str
    approved_at: datetime = set_on_insert()
