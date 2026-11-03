"""Join migrations after both branches added ``repos.enabled``.

Revision ID: 488d68b3f3b1
Revises: 4cb9a29a2d6f, b81f4b96a010
Create Date: 2026-11-03 16:34:22.378862
"""

from collections.abc import Sequence

revision: str = "488d68b3f3b1"
down_revision: str | Sequence[str] | None = ("4cb9a29a2d6f", "b81f4b96a010")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
