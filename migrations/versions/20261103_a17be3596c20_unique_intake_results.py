"""Make one intake result per PR head and rule idempotent.

Revision ID: a17be3596c20
Revises: 5c2eab19d4f1
Create Date: 2026-11-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a17be3596c20"
down_revision: str | Sequence[str] | None = "5c2eab19d4f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add idempotency for intake results sharing a pull request head."""
    op.create_unique_constraint(
        "uq_intake_results_pr_head_rule",
        "intake_results",
        ["pr_id", "head_sha", "rule"],
    )


def downgrade() -> None:
    """Remove the intake result uniqueness constraint."""
    op.drop_constraint("uq_intake_results_pr_head_rule", "intake_results", type_="unique")
