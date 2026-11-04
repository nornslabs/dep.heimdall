"""Triage tables: pull requests with their state, and the raw GitHub events received."""

import uuid
from datetime import datetime

from sqlalchemy import Index, UniqueConstraint
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


class Pr(TenantScoped, table=True):
    """A pull request and where it is in the triage state machine.

    ``state_version`` is bumped on every transition and checked in the UPDATE, so two
    workers can never both move the same PR. ``repro_done_head`` and ``review_done_head``
    hold the head each agent check last finished on; a check counts as done only while
    that equals ``head_sha``, so a push resets both without touching them.
    """

    __tablename__ = "prs"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        UniqueConstraint("repo_id", "number"),
        Index("ix_prs_installation_id_state", "installation_id", "state"),
    )

    id: uuid.UUID = uuid_pk()
    repo_id: int = bigint(foreign_key="repos.repo_id")
    number: int
    author: str
    head_sha: str
    base_sha: str
    base_ref: str | None = None
    linked_issue: int | None = None
    state: str
    state_version: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    switch_thread_id: str | None = None
    repro_done_head: str | None = None
    review_done_head: str | None = None
    created_at: datetime = set_on_insert()
    updated_at: datetime = set_on_insert()


class Event(TenantScoped, table=True):
    """A GitHub webhook delivery; the delivery id as key makes redeliveries a no-op."""

    __tablename__ = "events"  # pyright: ignore[reportAssignmentType]

    delivery_id: str = Field(primary_key=True)
    kind: str
    action: str | None = None
    payload: JsonObject = jsonb()
    received_at: datetime = set_on_insert()
    handled_at: datetime | None = timestamp()
