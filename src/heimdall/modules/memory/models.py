"""Memory tables: durable notes each agent keeps per repository."""

import uuid
from datetime import datetime

from sqlalchemy import Index, UniqueConstraint

from heimdall.adapters.db.columns import bigint, set_on_insert, timestamp, uuid_pk
from heimdall.adapters.db.tenant import TenantScoped


class AgentMemory(TenantScoped, table=True):
    """One maintainer-requested note, such as the repo's test command, for one agent.

    A key is unique per repo and agent, so remembering it again replaces the value.
    """

    __tablename__ = "agent_memory"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        UniqueConstraint("repo_id", "agent", "key"),
        Index(
            "ix_agent_memory_installation_id_repo_id_agent", "installation_id", "repo_id", "agent"
        ),
    )

    id: uuid.UUID = uuid_pk()
    repo_id: int = bigint(foreign_key="repos.repo_id")
    agent: str
    key: str
    value: str
    source_message_id: str
    created_by: str
    created_at: datetime = set_on_insert()
    expires_at: datetime | None = timestamp()
