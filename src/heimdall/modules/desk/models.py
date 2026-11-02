"""Desk tables: the PR card, drafts awaiting approval, and mechanical-fix tasks."""

import uuid
from datetime import datetime

from sqlmodel import Field

from heimdall.adapters.db.columns import set_on_insert, uuid_pk
from heimdall.adapters.db.tenant import TenantScoped


class Card(TenantScoped, table=True):
    """The summary card Heimdall posts for a PR head commit."""

    __tablename__ = "cards"  # pyright: ignore[reportAssignmentType]

    id: uuid.UUID = uuid_pk()
    pr_id: uuid.UUID = Field(foreign_key="prs.id")
    head_sha: str
    body: str
    created_at: datetime = set_on_insert()


class Draft(TenantScoped, table=True):
    """Text Heimdall wants to post on GitHub; ``body_hash`` is what an approval must match."""

    __tablename__ = "drafts"  # pyright: ignore[reportAssignmentType]

    id: uuid.UUID = uuid_pk()
    pr_id: uuid.UUID = Field(foreign_key="prs.id")
    kind: str
    body: str
    body_hash: str
    superseded_by: uuid.UUID | None = None
    created_at: datetime = set_on_insert()


class AgentTask(TenantScoped, table=True):
    """A mechanical fix handed to a coding agent; the id looks like ``HT-482-1``."""

    __tablename__ = "agent_tasks"  # pyright: ignore[reportAssignmentType]

    id: str = Field(primary_key=True)
    pr_id: uuid.UUID = Field(foreign_key="prs.id")
    finding_id: uuid.UUID = Field(foreign_key="findings.id")
    draft_id: uuid.UUID = Field(foreign_key="drafts.id")
    status: str
    created_at: datetime = set_on_insert()
