"""Cache the config fingerprint associated with a PR head.

Revision ID: 6d7e0c149d23
Revises: 488d68b3f3b1
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6d7e0c149d23"
down_revision: str | Sequence[str] | None = "488d68b3f3b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store the head SHA used to produce the current cached config."""
    op.add_column("repos", sa.Column("config_head_sha", sa.String(), nullable=True))


def downgrade() -> None:
    """Remove the config head cache key."""
    op.drop_column("repos", "config_head_sha")
