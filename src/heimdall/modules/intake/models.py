"""Intake tables: one row per house rule checked against a PR head commit."""

import uuid
from datetime import datetime

from sqlmodel import Field

from heimdall.adapters.db.columns import set_on_insert, uuid_pk
from heimdall.adapters.db.tenant import TenantScoped


class IntakeResult(TenantScoped, table=True):
    """The outcome of one intake rule for one PR head commit."""

    __tablename__ = "intake_results"  # pyright: ignore[reportAssignmentType]

    id: uuid.UUID = uuid_pk()
    pr_id: uuid.UUID = Field(foreign_key="prs.id")
    head_sha: str
    rule: str
    passed: bool
    detail: str | None = None
    checked_at: datetime = set_on_insert()
