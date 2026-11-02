"""Audit tables: the insert-only audit log and per-agent token usage."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Index
from sqlmodel import Field

from heimdall.adapters.db.columns import JsonObject, jsonb, set_on_insert
from heimdall.adapters.db.tenant import TenantScoped


class AuditLogEntry(TenantScoped, table=True):
    """One thing that happened, recorded forever; the database rejects updates and deletes."""

    __tablename__ = "audit_log"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        Index("ix_audit_log_installation_id_subject_at", "installation_id", "subject", "at"),
    )

    id: int | None = Field(default=None, sa_type=BigInteger, primary_key=True)
    at: datetime = set_on_insert()
    actor: str
    action: str
    subject: str
    payload: JsonObject = jsonb()


class Usage(TenantScoped, table=True):
    """Model tokens one agent spent, for cost reporting per installation."""

    __tablename__ = "usage"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (Index("ix_usage_installation_id_at", "installation_id", "at"),)

    id: int | None = Field(default=None, sa_type=BigInteger, primary_key=True)
    pr_id: uuid.UUID | None = None
    agent: str
    model: str
    input_tokens: int
    output_tokens: int
    at: datetime = set_on_insert()
