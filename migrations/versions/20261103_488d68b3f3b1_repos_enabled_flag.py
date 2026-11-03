"""repos.enabled: switch a repo off when it is removed from the installation

The row stays (pull requests reference it); existing repos default to enabled.

Revision ID: 488d68b3f3b1
Revises: 507f7ae60c4c
Create Date: 2026-11-03 16:34:22.378862
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "488d68b3f3b1"
down_revision: str | Sequence[str] | None = "507f7ae60c4c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "repos", sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False)
    )


def downgrade() -> None:
    op.drop_column("repos", "enabled")
