"""Conversation tables: one row per question put to an agent, answered or refused."""

import uuid
from datetime import datetime

from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field

from heimdall.adapters.db.columns import timestamp, uuid_pk
from heimdall.adapters.db.tenant import TenantScoped


class AgentQuestion(TenantScoped, table=True):
    """One question a sender put to an agent, kept so the next one can be counted.

    ``pr_id`` is empty for a question outside a PR thread, which is counted per ``room_id``.
    A message id is unique per agent, so a message Switch delivers twice is counted once.
    """

    __tablename__ = "agent_question"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        UniqueConstraint("installation_id", "agent", "message_id"),
        Index(
            "ix_agent_question_installation_id_agent_sender",
            "installation_id",
            "agent",
            "sender",
            "asked_at",
        ),
    )

    id: uuid.UUID = uuid_pk()
    agent: str
    sender: str
    pr_id: uuid.UUID | None = Field(default=None, foreign_key="prs.id")
    room_id: str
    message_id: str
    outcome: str
    asked_at: datetime = timestamp(nullable=False)
