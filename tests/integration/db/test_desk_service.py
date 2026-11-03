"""Integration coverage for tenant-scoped desk cards and drafts."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.desk.api import CardContent, create_mechanical_task, save_card, save_draft
from heimdall.modules.review.models import Finding
from heimdall.modules.tenancy.api import on_installation_event
from heimdall.modules.triage.api import EventKind, Facts, PrEvent, handle_event
from heimdall.modules.triage.models import Pr
from heimdall.shared.context import TenantCtx
from heimdall.shared.errors import StaleHeadSha
from heimdall.shared.ids import InstallationId, RepoId
from tests.integration.db.installation_helpers import created_installation

pytestmark = pytest.mark.integration


async def _open_pr(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[TenantCtx, uuid.UUID]:
    """Create tenant repository and PR fixture for desk cases."""
    installation_id = uuid.uuid4().int % 1_000_000_000 + 1_000_000
    repo_id = RepoId(installation_id + 1)
    ctx = TenantCtx(installation_id=InstallationId(installation_id), request_id="desk-test")
    await on_installation_event(
        sessions,
        created_installation(installation_id, "desk-test", repo_id, "desk-test/demo"),
    )
    await handle_event(
        sessions,
        ctx,
        PrEvent(
            event_id=f"open-{uuid.uuid4()}",
            repo_id=repo_id,
            repo_full_name="desk-test/demo",
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
            await session.exec(select_for_tenant(Pr, ctx.installation_id).where(Pr.number == 1))
        ).one()
        return ctx, pr.id


async def test_save_card_is_idempotent_for_same_head(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Return the existing card when the same head is rendered again."""
    ctx, pr_id = await _open_pr(sessions)
    content = CardContent(1, "Example", "head-1", "Ready", "Pending", ())

    first = await save_card(sessions, ctx, pr_id, content)
    second = await save_card(sessions, ctx, pr_id, content)

    assert first == second


async def test_save_draft_supersedes_prior_draft(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Keep one current draft per PR and kind while retaining its history."""
    ctx, pr_id = await _open_pr(sessions)

    previous = await save_draft(sessions, ctx, pr_id, "head-1", "summary", "Draft one")
    current = await save_draft(sessions, ctx, pr_id, "head-1", "summary", "Draft two")

    assert previous.id != current.id
    assert previous.body_hash != current.body_hash
    repeated = await save_draft(sessions, ctx, pr_id, "head-1", "summary", "Draft two")
    assert repeated == current


async def test_save_card_rejects_stale_head(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Do not persist a card whose PR head has moved."""
    ctx, pr_id = await _open_pr(sessions)
    content = CardContent(1, "Example", "old-head", "Ready", "Pending", ())

    with pytest.raises(StaleHeadSha):
        await save_card(sessions, ctx, pr_id, content)


async def test_create_mechanical_task_saves_idempotent_draft_block(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Create one tenant-scoped task, suggestion block and exact approval draft."""
    ctx, pr_id = await _open_pr(sessions)
    finding_id = uuid.uuid4()
    async with unit_of_work(sessions) as session:
        session.add(
            Finding(
                id=finding_id,
                installation_id=ctx.installation_id,
                pr_id=pr_id,
                head_sha="head-1",
                file="src/one.py",
                line=3,
                text="Use a bounded retry for this request.",
                severity="low",
                mechanical_category="lint",
            )
        )

    task = await create_mechanical_task(sessions, ctx, pr_id, "head-1", finding_id)
    repeated = await create_mechanical_task(sessions, ctx, pr_id, "head-1", finding_id)

    assert task == repeated
    assert task.id == "HT-1-1"
    assert task.draft_id
    assert task.category == "lint"
    assert "Suggestion: Use a bounded retry" in task.task_block
    assert 'heimdall-task id="HT-1-1"' in task.task_block
    assert len(task.body_hash) == 64
