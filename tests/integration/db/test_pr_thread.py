"""Integration coverage for the Switch thread each PR keeps."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from apps.worker._threads import SessionThreadStore
from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.tenancy.api import on_installation_event
from heimdall.modules.triage.api import (
    EventKind,
    Facts,
    PrEvent,
    get_switch_thread_id,
    handle_event,
    save_switch_thread_id,
)
from heimdall.modules.triage.models import Pr
from heimdall.shared.context import TenantCtx
from heimdall.shared.ids import InstallationId, RepoId
from tests.integration.db.installation_helpers import created_installation

pytestmark = pytest.mark.integration


async def _open_pr(sessions: async_sessionmaker[AsyncSession]) -> tuple[TenantCtx, uuid.UUID]:
    """Create an enabled repository and an opened PR, and return its tenant and row id."""
    installation_id = int(uuid.uuid4().int % 1_000_000_000) + 1_000_000
    repo_id = RepoId(installation_id + 1)
    ctx = TenantCtx(installation_id=InstallationId(installation_id), request_id="thread-test")
    await on_installation_event(
        sessions,
        created_installation(installation_id, "thread-test", repo_id, "thread-test/demo"),
    )
    await handle_event(
        sessions,
        ctx,
        PrEvent(
            event_id="open-thread",
            repo_id=repo_id,
            repo_full_name="thread-test/demo",
            number=1,
            author="octocat",
            head_sha="head-1",
            base_sha="base-1",
            base_ref="main",
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
        return ctx, pr.id


async def test_a_pr_has_no_thread_until_one_is_saved(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ctx, pr_id = await _open_pr(sessions)

    assert await get_switch_thread_id(sessions, ctx, pr_id) is None


async def test_a_saved_thread_can_be_read_back(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ctx, pr_id = await _open_pr(sessions)

    await save_switch_thread_id(sessions, ctx, pr_id, "sw_root")

    assert await get_switch_thread_id(sessions, ctx, pr_id) == "sw_root"


async def test_the_first_saved_thread_wins(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Two checklists racing to open a thread must not move replies to a second root."""
    ctx, pr_id = await _open_pr(sessions)

    await save_switch_thread_id(sessions, ctx, pr_id, "sw_first")
    await save_switch_thread_id(sessions, ctx, pr_id, "sw_second")

    assert await get_switch_thread_id(sessions, ctx, pr_id) == "sw_first"


async def test_another_tenant_can_neither_read_nor_change_the_thread(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ctx, pr_id = await _open_pr(sessions)
    other = TenantCtx(installation_id=InstallationId(ctx.installation_id + 7), request_id="other")
    await save_switch_thread_id(sessions, ctx, pr_id, "sw_root")

    await save_switch_thread_id(sessions, other, pr_id, "sw_hijack")

    assert await get_switch_thread_id(sessions, other, pr_id) is None
    assert await get_switch_thread_id(sessions, ctx, pr_id) == "sw_root"


async def test_the_worker_thread_store_reads_and_keeps_the_first_thread(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The routing sender talks to this store with plain ids, as it finds them in outbox rows."""
    ctx, pr_id = await _open_pr(sessions)
    store = SessionThreadStore(sessions)

    assert await store.get(ctx.installation_id, str(pr_id)) is None

    await store.save(ctx.installation_id, str(pr_id), "sw_first")
    await store.save(ctx.installation_id, str(pr_id), "sw_second")

    assert await store.get(ctx.installation_id, str(pr_id)) == "sw_first"
