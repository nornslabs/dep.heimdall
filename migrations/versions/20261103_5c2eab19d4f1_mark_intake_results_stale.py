"""Track intake results superseded by a pull request head update.

Revision ID: 5c2eab19d4f1
Revises: 4cb9a29a2d6f
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5c2eab19d4f1"
down_revision: str | Sequence[str] | None = "4cb9a29a2d6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add an explicit stale marker to saved intake rule results."""
    op.add_column(
        "intake_results",
        sa.Column("stale", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    """Remove the stale marker from intake results."""
    op.drop_column("intake_results", "stale")
