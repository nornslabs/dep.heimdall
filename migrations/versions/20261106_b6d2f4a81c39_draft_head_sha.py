"""Add drafts.head_sha: the PR head a draft was prepared against.

A merge draft is approved for one exact head; the sender passes it to GitHub so a PR that
moved after approval is refused instead of merged.

Revision ID: b6d2f4a81c39
Revises: a91c5d0e7f23
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b6d2f4a81c39"
down_revision: str | Sequence[str] | None = "a91c5d0e7f23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable head column; drafts saved before it cannot be merged."""
    op.add_column("drafts", sa.Column("head_sha", sa.String(), nullable=True))


def downgrade() -> None:
    """Drop the head column."""
    op.drop_column("drafts", "head_sha")
