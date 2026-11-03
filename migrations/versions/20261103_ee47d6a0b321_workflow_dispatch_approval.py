"""Authorize exact reproduction workflow dispatches.

Revision ID: ee47d6a0b321
Revises: b81f4b96a010, 6d7e0c149d23
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import alembic.op as op
import sqlalchemy as sa

revision: str = "ee47d6a0b321"
down_revision: str | Sequence[str] | None = ("b81f4b96a010", "6d7e0c149d23")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create tenant scoped authorization records for Actions dispatches."""
    op.create_table(
        "workflow_dispatch_approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("repro_run_id", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(), nullable=False),
        sa.Column("approver", sa.String(), nullable=False),
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["repro_run_id"], ["repro_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repro_run_id"),
    )


def downgrade() -> None:
    """Remove workflow dispatch authorizations."""
    op.drop_table("workflow_dispatch_approvals")
