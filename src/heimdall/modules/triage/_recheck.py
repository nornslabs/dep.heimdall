"""A maintainer's request to run a PR's intake again."""

from dataclasses import replace
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.triage._states import RejectReason
from heimdall.modules.triage.domain import EventKind, Facts, PrEvent, Rejected, Transition
from heimdall.modules.triage.models import Pr
from heimdall.modules.triage.service import apply_existing_event
from heimdall.shared.context import TenantCtx
from heimdall.shared.ids import RepoId


async def request_recheck(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    pr_id: UUID,
    repo_full_name: str,
) -> Transition | Rejected:
    """Run a PR's intake again on its current head, through the state machine.

    The caller has already established that the person asking is a maintainer; the agent
    runtime opens the tool that calls this only for maintainers. Intake is queued for the
    stored head, not a head the caller names, so a moved head is rechecked as it now is.

    Args:
        sessions: Session factory for the transaction.
        ctx: Tenant and request context; supplies installation_id.
        pr_id: The PR to recheck.
        repo_full_name: ``owner/name`` of the PR's repository, which the intake job needs.

    Returns:
        The transition, or the reason the state machine refused it (for example a PR with a
        draft awaiting approval, or one already closed).
    """
    async with unit_of_work(sessions) as session:
        result = await session.exec(
            select_for_tenant(Pr, ctx.installation_id).where(Pr.id == pr_id).with_for_update()
        )
        pr = result.first()
        if pr is None:
            return Rejected(RejectReason.NOT_ALLOWED_FROM_STATE)
        event = PrEvent(
            event_id=f"recheck:{ctx.request_id}",
            repo_id=RepoId(pr.repo_id),
            number=pr.number,
            author=pr.author,
            head_sha=pr.head_sha,
            base_sha=pr.base_sha,
            base_ref=pr.base_ref,
            kind=EventKind.MAINTAINER_RECHECK,
            facts=replace(Facts(), repo_enabled=True, sender_is_maintainer=True),
            linked_issue=pr.linked_issue,
            repo_full_name=repo_full_name,
        )
        return await apply_existing_event(session, ctx, pr, event)
