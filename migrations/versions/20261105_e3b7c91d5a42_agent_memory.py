"""Add agent_memory: durable notes each agent keeps per repository.

A maintainer asks an agent to remember a repo fact (for example the test command); the note
outlives the PR so the agent can recall it on the next one. One value per repo, agent and key.

Revision ID: e3b7c91d5a42
Revises: c4e8a1f20b57
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3b7c91d5a42"
down_revision: str | Sequence[str] | None = "c4e8a1f20b57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_agent_memory_installation_id_repo_id_agent"


def upgrade() -> None:
    """Create the agent_memory table, its key uniqueness and its tenant lookup index."""
    op.create_table(
        "agent_memory",
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.BigInteger(), nullable=False),
        sa.Column("agent", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.String(), nullable=False),
        sa.Column("source_message_id", sa.String(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["repo_id"], ["repos.repo_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repo_id", "agent", "key"),
    )
    op.create_index(_INDEX, "agent_memory", ["installation_id", "repo_id", "agent"], unique=False)


def downgrade() -> None:
    """Drop the agent_memory table and its index."""
    op.drop_index(_INDEX, table_name="agent_memory")
    op.drop_table("agent_memory")
