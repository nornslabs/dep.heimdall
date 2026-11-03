"""Store the branch ref required by workflow_dispatch.

Revision ID: 2c1f9d3a7b4e
Revises: ee47d6a0b321
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2c1f9d3a7b4e"
down_revision: str | Sequence[str] | None = "ee47d6a0b321"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the pull request base branch to triage records."""
    op.add_column("prs", sa.Column("base_ref", sa.String(), nullable=True))


def downgrade() -> None:
    """Remove the pull request base branch."""
    op.drop_column("prs", "base_ref")
