"""Store which head Huginn's repro and Loki's review last finished on."""

from uuid import UUID

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.modules.triage.domain import CheckHeads, EventKind
from heimdall.modules.triage.models import Pr
from heimdall.shared.context import TenantCtx

# Column names are fixed here, never taken from input.
_CHECK_COLUMNS = {
    EventKind.REPRO_FINISHED: "repro_done_head",
    EventKind.REVIEW_FINISHED: "review_done_head",
}
_MARK_DONE = (
    "UPDATE prs SET {column} = :head_sha "
    "WHERE id = :pr_id AND installation_id = :installation_id AND head_sha = :head_sha"
)


def stored_check_heads(pr: Pr) -> CheckHeads:
    """Read the finished-check heads off a loaded PR row."""
    return CheckHeads(repro=pr.repro_done_head, review=pr.review_done_head)


async def mark_check_done(
    session: AsyncSession, ctx: TenantCtx, pr_id: UUID, kind: EventKind, head_sha: str
) -> None:
    """Record that a check finished on ``head_sha``, if that is still the PR's head.

    Args:
        session: Session inside the step's transaction.
        ctx: Tenant context that scopes the update.
        pr_id: PR whose check finished.
        kind: ``REPRO_FINISHED`` or ``REVIEW_FINISHED``; other events are ignored.
        head_sha: Commit the check evaluated.
    """
    column = _CHECK_COLUMNS.get(kind)
    if column is None:
        return
    connection = await session.connection()
    await connection.execute(
        text(_MARK_DONE.format(column=column)),
        {"head_sha": head_sha, "pr_id": pr_id, "installation_id": ctx.installation_id},
    )
