"""Remember the head each agent check finished on, so triage can tell when both are done.

Revision ID: 7a3e5d21c9b0
Revises: 2c1f9d3a7b4e
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7a3e5d21c9b0"
down_revision: str | Sequence[str] | None = "2c1f9d3a7b4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the head SHAs Huginn's repro and Loki's review last finished on."""
    op.add_column("prs", sa.Column("repro_done_head", sa.String(), nullable=True))
    op.add_column("prs", sa.Column("review_done_head", sa.String(), nullable=True))


def downgrade() -> None:
    """Drop the finished-check heads."""
    op.drop_column("prs", "review_done_head")
    op.drop_column("prs", "repro_done_head")
