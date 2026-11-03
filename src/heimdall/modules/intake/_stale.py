"""Mark stored intake checks superseded by a newer pull request head."""

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.shared.context import TenantCtx

MARK_INTAKE_STALE = text(
    """
    UPDATE intake_results SET stale = true
    WHERE installation_id = :installation_id AND pr_id = :pr_id
        AND head_sha <> :head_sha AND stale = false
    """
)


async def mark_results_stale(
    session: AsyncSession,
    ctx: TenantCtx,
    pr_id: str,
    head_sha: str,
) -> None:
    """Mark old head results stale inside the caller's state transaction.

    Args:
        session: Session inside the triage transaction.
        ctx: Tenant context for the result rows.
        pr_id: PR whose checks were superseded.
        head_sha: New current head commit.
    """
    connection = await session.connection()
    await connection.execute(
        MARK_INTAKE_STALE,
        {
            "installation_id": ctx.installation_id,
            "pr_id": pr_id,
            "head_sha": head_sha,
        },
    )
