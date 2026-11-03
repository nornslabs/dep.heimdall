"""Persist maintainer pauses for automatic mechanical suggestions.

Revision ID: b81f4b96a010
Revises: a17be3596c20
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b81f4b96a010"
down_revision: str | Sequence[str] | None = "a17be3596c20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the repository-scoped policy pause flag."""
    op.add_column(
        "repos",
        sa.Column(
            "auto_suggest_paused",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """Remove the repository-scoped policy pause flag."""
    op.drop_column("repos", "auto_suggest_paused")
