"""Repro tables: reproduction runs on GitHub Actions, with and without the fix."""

import uuid
from datetime import datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field

from heimdall.adapters.db.columns import (
    JsonObject,
    bigint,
    jsonb,
    set_on_insert,
    timestamp,
    uuid_pk,
)
from heimdall.adapters.db.tenant import TenantScoped


class ReproRun(TenantScoped, table=True):
    """One attempt to reproduce a PR's fix: test results with and without it, and a verdict."""

    __tablename__ = "repro_runs"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (UniqueConstraint("pr_id", "head_sha", "attempt"),)

    id: uuid.UUID = uuid_pk()
    pr_id: uuid.UUID = Field(foreign_key="prs.id")
    head_sha: str
    attempt: int
    test_cmd: str
    gh_run_id: int | None = bigint(nullable=True)
    status: str
    with_fix: JsonObject | None = jsonb(nullable=True)
    without_fix: JsonObject | None = jsonb(nullable=True)
    verdict: str | None = None
    started_at: datetime = set_on_insert()
    finished_at: datetime | None = timestamp()
