"""Give each repository the Switch room UUID its agents post to.

The bot posts to a Mattermost channel id (``switch_room_id``); a Switch agent only accepts
Switch's own room UUID, so the two cannot share one column.

Revision ID: c4e8a1f20b57
Revises: 7a3e5d21c9b0
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8a1f20b57"
down_revision: str | Sequence[str] | None = "7a3e5d21c9b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable agent room column."""
    op.add_column("repos", sa.Column("switch_agent_room_id", sa.String(), nullable=True))


def downgrade() -> None:
    """Remove the agent room column."""
    op.drop_column("repos", "switch_agent_room_id")
