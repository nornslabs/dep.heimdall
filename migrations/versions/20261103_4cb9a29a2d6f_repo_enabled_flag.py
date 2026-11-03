"""Track whether an installation still grants access to a repository.

Revision ID: 4cb9a29a2d6f
Revises: 507f7ae60c4c
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4cb9a29a2d6f"
down_revision: str | Sequence[str] | None = "507f7ae60c4c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add an enabled flag, keeping existing installed repositories enabled."""
    op.add_column(
        "repos",
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def downgrade() -> None:
    """Remove the installation-scoped repository status."""
    op.drop_column("repos", "enabled")
