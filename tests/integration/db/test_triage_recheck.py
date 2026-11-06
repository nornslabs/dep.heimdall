"""A maintainer's recheck moves a PR back to intake through the state machine."""

from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from agents._mcp_tools import McpToolClient
from apps.mcp.main import create_server
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.adapters.github.reader import GitHubApiReader
from heimdall.modules.triage.api import (
    EventKind,
    Rejected,
    Transition,
    get_pr_snapshot,
    handle_event,
    request_recheck,
)
from heimdall.shared.context import TenantCtx, new_request_id
from tests.integration.db.test_triage_service import (
    _create_enabled_repo,  # pyright: ignore[reportPrivateUsage]
    _fail_intake,  # pyright: ignore[reportPrivateUsage]
    _pr_event,  # pyright: ignore[reportPrivateUsage]
    _queued_tasks,  # pyright: ignore[reportPrivateUsage]
)

pytestmark = pytest.mark.integration

REPO_FULL_NAME = "triage-test/demo"


def _recheck_ctx(ctx: TenantCtx) -> TenantCtx:
    """Give each recheck its own request id, as every mention job has."""
    return TenantCtx(installation_id=ctx.installation_id, request_id=new_request_id())


async def test_recheck_of_a_failed_pr_queues_intake_again(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    ctx, repo_id = await _create_enabled_repo(sessions)
    pr_id = await _fail_intake(sessions, ctx, repo_id, "recheck-failed")

    result = await request_recheck(sessions, _recheck_ctx(ctx), pr_id, REPO_FULL_NAME)

    assert isinstance(result, Transition)
    async with unit_of_work(sessions) as session:
        pr = await get_pr_snapshot(session, ctx, pr_id)
    assert pr is not None and pr.state.value == "received"
    assert (await _queued_tasks(engine, pr_id)).count("intake.run_intake") == 2


async def test_recheck_of_a_closed_pr_changes_nothing(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    ctx, repo_id = await _create_enabled_repo(sessions)
    pr_id = await _fail_intake(sessions, ctx, repo_id, "recheck-closed")
    await handle_event(
        sessions, ctx, _pr_event(repo_id, EventKind.PR_CLOSED, "head-1", "recheck-closed-close")
    )

    result = await request_recheck(sessions, _recheck_ctx(ctx), pr_id, REPO_FULL_NAME)

    assert isinstance(result, Rejected)
    assert (await _queued_tasks(engine, pr_id)).count("intake.run_intake") == 1


async def test_recheck_of_another_tenants_pr_is_refused(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    ctx, repo_id = await _create_enabled_repo(sessions)
    other_ctx, _ = await _create_enabled_repo(sessions)
    pr_id = await _fail_intake(sessions, ctx, repo_id, "recheck-tenant")

    result = await request_recheck(sessions, _recheck_ctx(other_ctx), pr_id, REPO_FULL_NAME)

    assert isinstance(result, Rejected)
    assert (await _queued_tasks(engine, pr_id)).count("intake.run_intake") == 1


async def test_the_recheck_tool_on_heimdalls_server_queues_intake(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    ctx, repo_id = await _create_enabled_repo(sessions)
    pr_id = await _fail_intake(sessions, ctx, repo_id, "recheck-tool")
    server = create_server(sessions, cast(GitHubApiReader, object()), agent_name="heimdall")

    accepted = await McpToolClient(server).call(
        "rerun_checks",
        installation_id=ctx.installation_id,
        pr_id=str(pr_id),
        repo_full_name=REPO_FULL_NAME,
    )

    assert accepted == {"rechecked": "yes", "was": "intake_failed", "state": "received"}
    assert (await _queued_tasks(engine, pr_id)).count("intake.run_intake") == 2
    refused = await McpToolClient(server).call(
        "rerun_checks",
        installation_id=ctx.installation_id,
        pr_id=str(pr_id),
        repo_full_name=REPO_FULL_NAME,
    )
    assert refused == {
        "rechecked": "no",
        "reason": "not_allowed_from_state",
        "was": "received",
    }
    assert (await _queued_tasks(engine, pr_id)).count("intake.run_intake") == 2
