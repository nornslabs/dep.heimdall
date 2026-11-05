"""Record that Loki finished reviewing a PR head, even when it saved nothing."""

import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.triage.api import (
    EventKind,
    Facts,
    get_pr_snapshot,
    on_step_done_in_transaction,
)
from heimdall.shared.context import TenantCtx
from heimdall.shared.errors import StaleHeadSha


async def finish_review(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    pr_id: uuid.UUID,
    head_sha: str,
    event_id: str,
) -> None:
    """Tell triage that Loki reviewed this head, so a clean pass still advances the card.

    ``save_findings`` reports this itself once it stores a finding. A review with nothing to
    save has no such call, so the runner reports the finished step here.

    Args:
        sessions: Tenant database session factory.
        ctx: Tenant and request context.
        pr_id: Triage PR row id.
        head_sha: Commit Loki reviewed.
        event_id: Stable job result idempotency key.

    Raises:
        StaleHeadSha: the PR head changed before the review was recorded.
    """
    async with unit_of_work(sessions) as session:
        pr = await get_pr_snapshot(session, ctx, pr_id)
        if pr is None or pr.head_sha != head_sha:
            raise StaleHeadSha("pull request head changed before the review finished")
        await on_step_done_in_transaction(
            session,
            ctx,
            pr_id,
            event_id,
            EventKind.REVIEW_FINISHED,
            head_sha,
            Facts(review_done=True),
        )
