"""Integration coverage for tenant scoped, head scoped findings."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.review.api import FindingInput, list_findings, save_findings
from heimdall.modules.review.models import Finding
from heimdall.modules.tenancy.api import (
    ReviewConfig,
    on_installation_event,
)
from heimdall.modules.triage.api import EventKind, Facts, PrEvent, handle_event, on_step_done
from heimdall.modules.triage.models import Pr
from heimdall.shared.context import TenantCtx
from heimdall.shared.github import ChangedFile, RepoRef
from heimdall.shared.ids import InstallationId, RepoId
from tests.integration.db.installation_helpers import created_installation

pytestmark = pytest.mark.integration


class ChangedFileReader:
    """Return the one path the fixture PR actually changes."""

    async def list_changed_files(self, repo: RepoRef, number: int) -> tuple[ChangedFile, ...]:
        """Return changed-file values from the fake diff."""
        return (ChangedFile("src/app.py", "modified", 5, 1),)


async def test_save_findings_persists_only_valid_current_head_results(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Validate citations, save accepted findings once, and finish review."""
    installation_id = int(uuid.uuid4().int % 1_000_000_000) + 1_000_000
    repo_id = RepoId(installation_id + 1)
    ctx = TenantCtx(installation_id=InstallationId(installation_id), request_id="review-test")
    await on_installation_event(
        sessions,
        created_installation(installation_id, "review-test", repo_id, "review-test/demo"),
    )
    await handle_event(
        sessions,
        ctx,
        PrEvent(
            event_id="open-review",
            repo_id=repo_id,
            repo_full_name="review-test/demo",
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
                select_for_tenant(Pr, ctx.installation_id).where(
                    Pr.repo_id == repo_id,
                    Pr.number == 1,
                )
            )
        ).one()
        pr_id = pr.id
    await on_step_done(
        sessions,
        ctx,
        pr_id,
        "intake-review",
        EventKind.INTAKE_DONE,
        "head-1",
        Facts(intake_passed=True),
    )
    findings = (
        FindingInput("src/app.py", 12, "Check the empty response", "warning"),
        FindingInput("src/secret.py", 2, "This file is not in the PR", "error"),
    )
    repo = RepoRef(ctx.installation_id, "review-test/demo")

    batch = await save_findings(
        sessions,
        ChangedFileReader(),
        ctx,
        repo,
        pr_id,
        "head-1",
        "review-job-1",
        findings,
        ReviewConfig(),
    )
    await save_findings(
        sessions,
        ChangedFileReader(),
        ctx,
        repo,
        pr_id,
        "head-1",
        "review-job-1",
        findings,
        ReviewConfig(),
    )

    assert batch.accepted == (findings[0],)
    assert batch.rejected[0].reason == "file_not_changed"
    listed = await list_findings(sessions, ctx, pr_id)
    assert len(listed) == 1
    assert listed[0].file == "src/app.py"
    async with unit_of_work(sessions) as session:
        result = await session.exec(
            select_for_tenant(Finding, ctx.installation_id).where(
                Finding.pr_id == pr_id,
                Finding.head_sha == "head-1",
            )
        )
        assert len(result.all()) == 1
