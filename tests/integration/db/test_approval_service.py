"""Integration coverage for maintainer and exact-draft approval checks."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.outbox.models import OutboxMessage
from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.approval.api import get_approved_draft, record_approval
from heimdall.modules.desk.api import get_draft, save_draft
from heimdall.modules.tenancy.api import on_installation_event
from heimdall.modules.triage.api import EventKind, Facts, PrEvent, handle_event
from heimdall.modules.triage.models import Pr
from heimdall.shared.context import TenantCtx
from heimdall.shared.errors import DraftChanged, NotAMaintainer
from heimdall.shared.github import RepoRef
from heimdall.shared.ids import InstallationId, RepoId
from tests.integration.db.installation_helpers import created_installation

pytestmark = pytest.mark.integration


class MaintainerConfigReader:
    """Provide a repository config with one maintainer for approval tests."""

    async def get_file(self, repo: RepoRef, path: str, ref: str | None) -> str | None:
        """Return test config only at the repository config path."""
        if path == "heimdall.yml":
            return "maintainers:\n  - reviewer\n"
        return None


async def _draft_context(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[TenantCtx, RepoRef, uuid.UUID, uuid.UUID]:
    """Create an enabled repo and current-head draft."""
    installation_id = uuid.uuid4().int % 1_000_000_000 + 1_000_000
    repo_id = RepoId(installation_id + 1)
    ctx = TenantCtx(installation_id=InstallationId(installation_id), request_id="approval-test")
    repo = RepoRef(ctx.installation_id, "approval-test/demo")
    await on_installation_event(
        sessions,
        created_installation(installation_id, "approval-test", repo_id, repo.full_name),
    )
    await handle_event(
        sessions,
        ctx,
        PrEvent(
            event_id=f"open-{uuid.uuid4()}",
            repo_id=repo_id,
            repo_full_name=repo.full_name,
            number=1,
            author="octocat",
            head_sha="head-1",
            base_sha="base-1",
            kind=EventKind.PR_OPENED,
            facts=Facts(),
        ),
    )
    async with unit_of_work(sessions) as session:
        pr = (
            await session.exec(
                select_for_tenant(Pr, ctx.installation_id).where(Pr.repo_id == repo_id)
            )
        ).one()
        pr_id = pr.id
    draft = await save_draft(sessions, ctx, pr_id, "head-1", "summary", "Reviewed text")
    return ctx, repo, pr_id, draft.id


async def test_record_approval_checks_maintainer_and_exact_hash(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Save an approval once and reject non-maintainer or changed-text attempts."""
    ctx, repo, _, draft_id = await _draft_context(sessions)
    reader = MaintainerConfigReader()
    draft = await get_draft(sessions, ctx, draft_id)
    with pytest.raises(NotAMaintainer):
        await record_approval(sessions, reader, ctx, repo, draft_id, "stranger", draft.body_hash)
    with pytest.raises(DraftChanged):
        await record_approval(sessions, reader, ctx, repo, draft_id, "reviewer", "stale-hash")

    approval = await record_approval(
        sessions, reader, ctx, repo, draft_id, "Reviewer", draft.body_hash
    )
    repeated = await record_approval(
        sessions, reader, ctx, repo, draft_id, "reviewer", draft.body_hash
    )

    assert approval.draft_hash == draft.body_hash
    assert repeated.id == approval.id
    approved = await get_approved_draft(sessions, ctx, draft_id, draft.body_hash)
    assert approved.body == draft.body
    async with unit_of_work(sessions) as session:
        queued = await session.exec(
            select_for_tenant(OutboxMessage, ctx.installation_id).where(
                OutboxMessage.kind == "github.pr_comment"
            )
        )
        messages = queued.all()
    assert len(messages) == 1
    assert messages[0].payload["draft_hash"] == draft.body_hash
