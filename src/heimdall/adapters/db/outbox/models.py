"""The transactional outbox: side effects (GitHub, Switch) written with the state change.

Any module writes rows here in its own transaction; the worker's sender delivers them.
"""

import uuid
from datetime import datetime

from sqlalchemy import Index
from sqlmodel import Field

from heimdall.adapters.db.columns import JsonObject, jsonb, set_on_insert, timestamp, uuid_pk
from heimdall.adapters.db.tenant import TenantScoped


class OutboxMessage(TenantScoped, table=True):
    """One message waiting to be sent to GitHub or Switch, exactly once.

    ``channel`` is ``github`` or ``switch``; ``status`` moves pending → sent, or → dead
    after the last retry.
    """

    __tablename__ = "outbox"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (Index("ix_outbox_status_next_attempt_at", "status", "next_attempt_at"),)

    id: uuid.UUID = uuid_pk()
    channel: str
    kind: str
    payload: JsonObject = jsonb()
    idempotency_key: str = Field(unique=True)
    status: str
    attempts: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    next_attempt_at: datetime = set_on_insert()
    last_error: str | None = None
    created_at: datetime = set_on_insert()
    sent_at: datetime | None = timestamp()
