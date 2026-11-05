"""Add agent_question: one row per question put to an agent, for the rate limit.

Revision ID: a91c5d0e7f23
Revises: e3b7c91d5a42
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a91c5d0e7f23"
down_revision: str | Sequence[str] | None = "e3b7c91d5a42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_agent_question_installation_id_agent_sender"


def upgrade() -> None:
    """Create agent_question with its redelivery uniqueness and window lookup index."""
    op.create_table(
        "agent_question",
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent", sa.String(), nullable=False),
        sa.Column("sender", sa.String(), nullable=False),
        sa.Column("pr_id", sa.Uuid(), nullable=True),
        sa.Column("room_id", sa.String(), nullable=False),
        sa.Column("message_id", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["pr_id"], ["prs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("installation_id", "agent", "message_id"),
    )
    op.create_index(
        _INDEX,
        "agent_question",
        ["installation_id", "agent", "sender", "asked_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop agent_question and its index."""
    op.drop_index(_INDEX, table_name="agent_question")
    op.drop_table("agent_question")
