"""Read approved exact draft text for the GitHub outbox sender."""

import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.approval.domain import ApprovedDraft, validate_posting_hash
from heimdall.modules.approval.models import Approval
from heimdall.modules.desk.api import get_draft_in_transaction, hash_draft
from heimdall.shared.context import TenantCtx
from heimdall.shared.errors import DraftChanged


async def get_approved_draft(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    draft_id: uuid.UUID,
    queued_hash: str,
) -> ApprovedDraft:
    """Return exact draft text only after rechecking its approval and hashes.

    Args:
        sessions: Tenant database session factory.
        ctx: Tenant and request context.
        draft_id: Draft named by the outbox message.
        queued_hash: Hash captured when approval queued delivery.

    Returns:
        Exact draft body, PR id, and matching approval details.

    Raises:
        DraftChanged: No approval exists, the draft changed, or it was superseded.
    """
    async with unit_of_work(sessions) as session:
        result = await session.exec(
            select_for_tenant(Approval, ctx.installation_id).where(Approval.draft_id == draft_id)
        )
        approval = result.first()
        if approval is None:
            raise DraftChanged("draft has no approval")
        draft = await get_draft_in_transaction(session, ctx, draft_id)
        validate_posting_hash(
            approval.draft_hash,
            queued_hash,
            draft.body_hash,
            hash_draft(draft.body),
            superseded=draft.superseded_by is not None,
        )
        return ApprovedDraft(
            draft_id=draft.id,
            pr_id=draft.pr_id,
            body=draft.body,
            body_hash=draft.body_hash,
            approver=approval.approver,
            via=approval.via,
        )
