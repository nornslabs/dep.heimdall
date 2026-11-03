"""Mark reproduction runs superseded by a newer pull request head."""

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.shared.context import TenantCtx

MARK_REPRO_STALE = text(
    """
    UPDATE repro_runs SET status = 'stale'
    WHERE installation_id = :installation_id AND pr_id = :pr_id
        AND head_sha <> :head_sha AND status <> 'stale'
    """
)


async def mark_runs_stale(
    session: AsyncSession,
    ctx: TenantCtx,
    pr_id: str,
    head_sha: str,
) -> None:
    """Mark runs for an older PR head stale inside the caller's transaction.

    Args:
        session: Session inside the triage transaction.
        ctx: Tenant context for the run rows.
        pr_id: PR whose runs were superseded.
        head_sha: New current head commit.
    """
    connection = await session.connection()
    await connection.execute(
        MARK_REPRO_STALE,
        {
            "installation_id": ctx.installation_id,
            "pr_id": pr_id,
            "head_sha": head_sha,
        },
    )
