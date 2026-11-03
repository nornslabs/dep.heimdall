"""append_in_transaction and list_for_pr keep a tenant-scoped, ordered audit trail per PR."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.audit.api import AuditEntry, append_in_transaction, list_for_pr, pr_subject
from heimdall.shared.context import PrRef, TenantCtx
from heimdall.shared.ids import InstallationId, RepoId

pytestmark = pytest.mark.integration


class CallerFailedError(Exception):
    """Raised by a test to roll back the caller's transaction."""


def _tenant(request_id: str) -> TenantCtx:
    installation_id = InstallationId(int(uuid.uuid4().int % 1_000_000_000) + 3_000_000)
    return TenantCtx(installation_id=installation_id, request_id=request_id)


def _entry(pr_ref: PrRef, action: str) -> AuditEntry:
    return AuditEntry("reviewer", action, pr_subject(pr_ref), {"draft_id": "d-1"})


async def test_list_for_pr_returns_this_prs_entries_oldest_first(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ctx = _tenant("audit-order")
    pr_ref = PrRef(RepoId(11), 5)
    other_pr = PrRef(RepoId(11), 6)

    async with unit_of_work(sessions) as session:
        await append_in_transaction(session, ctx, _entry(pr_ref, "draft.saved"))
        await append_in_transaction(session, ctx, _entry(other_pr, "draft.saved"))
    async with unit_of_work(sessions) as session:
        await append_in_transaction(session, ctx, _entry(pr_ref, "draft.approved"))

    entries = await list_for_pr(sessions, ctx, pr_ref)

    assert [entry.action for entry in entries] == ["draft.saved", "draft.approved"]
    assert entries[0].actor == "reviewer"
    assert entries[0].subject == "pr:11#5"
    assert entries[0].payload == {"draft_id": "d-1"}
    assert entries[0].at <= entries[1].at


async def test_append_is_rolled_back_with_the_caller_transaction(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ctx = _tenant("audit-rollback")
    pr_ref = PrRef(RepoId(12), 1)

    with pytest.raises(CallerFailedError):
        async with unit_of_work(sessions) as session:
            await append_in_transaction(session, ctx, _entry(pr_ref, "draft.approved"))
            raise CallerFailedError

    assert await list_for_pr(sessions, ctx, pr_ref) == []


async def test_list_for_pr_never_returns_another_tenants_rows(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    tenant_a = _tenant("audit-tenant-a")
    tenant_b = TenantCtx(
        installation_id=InstallationId(tenant_a.installation_id + 1),
        request_id="audit-tenant-b",
    )
    pr_ref = PrRef(RepoId(13), 2)

    async with unit_of_work(sessions) as session:
        await append_in_transaction(session, tenant_a, _entry(pr_ref, "draft.approved"))

    assert await list_for_pr(sessions, tenant_b, pr_ref) == []
    assert len(await list_for_pr(sessions, tenant_a, pr_ref)) == 1
