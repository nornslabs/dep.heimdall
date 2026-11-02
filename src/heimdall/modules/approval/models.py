"""Approval tables: who approved which draft, against which hash."""

import uuid
from datetime import datetime

from sqlmodel import Field

from heimdall.adapters.db.columns import set_on_insert, uuid_pk
from heimdall.adapters.db.tenant import TenantScoped


class Approval(TenantScoped, table=True):
    """A maintainer's (or policy's) approval of one draft; Gjallarhorn posts only with one.

    ``via`` is ``reaction`` or ``policy:auto_suggest``.
    """

    __tablename__ = "approvals"  # pyright: ignore[reportAssignmentType]

    id: uuid.UUID = uuid_pk()
    draft_id: uuid.UUID = Field(foreign_key="drafts.id", unique=True)
    approver: str
    draft_hash: str
    via: str
    approved_at: datetime = set_on_insert()
