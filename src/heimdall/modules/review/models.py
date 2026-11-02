"""Review tables: Loki's findings, each citing a file and line in the PR diff."""

import uuid
from datetime import datetime

from sqlalchemy import Index
from sqlmodel import Field

from heimdall.adapters.db.columns import set_on_insert, uuid_pk
from heimdall.adapters.db.tenant import TenantScoped


class Finding(TenantScoped, table=True):
    """One review finding at ``file:line`` for a PR head commit."""

    __tablename__ = "findings"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (Index("ix_findings_pr_id_head_sha", "pr_id", "head_sha"),)

    id: uuid.UUID = uuid_pk()
    pr_id: uuid.UUID = Field(foreign_key="prs.id")
    head_sha: str
    file: str
    line: int
    text: str
    severity: str
    mechanical_category: str | None = None
    created_at: datetime = set_on_insert()
