"""Integration tests for installation and repository lifecycle handling."""

from dataclasses import replace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.tenancy.api import (
    InstallationEvent,
    InstallationRepo,
    get_tenant,
    on_installation_event,
)
from heimdall.modules.tenancy.models import Repo
from heimdall.shared.errors import InstallationSuspended
from heimdall.shared.ids import InstallationId

pytestmark = pytest.mark.integration


async def test_installation_events_enable_disable_and_suspend_repos(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Repository access follows install, remove, suspend and unsuspend events."""
    installation_id = 9032
    tenant_id = InstallationId(installation_id)
    event = InstallationEvent(
        installation_id=installation_id,
        account_login="octo-org",
        account_type="Organization",
        event="installation",
        action="created",
        repositories=(
            InstallationRepo(repo_id=9_032_001, full_name="octo-org/one"),
            InstallationRepo(repo_id=9_032_002, full_name="octo-org/two"),
        ),
    )
    await on_installation_event(sessions, event)
    other_installation_id = 9033
    other_tenant_id = InstallationId(other_installation_id)
    await on_installation_event(
        sessions,
        InstallationEvent(
            installation_id=other_installation_id,
            account_login="other-org",
            account_type="Organization",
            event="installation",
            action="created",
            repositories=(InstallationRepo(repo_id=9_033_001, full_name="other-org/repo"),),
        ),
    )
    assert (await get_tenant(sessions, tenant_id)).installation_id == tenant_id

    await on_installation_event(
        sessions,
        InstallationEvent(
            installation_id=installation_id,
            account_login="octo-org",
            account_type="Organization",
            event="installation_repositories",
            action="removed",
            removed_repo_ids=(9_032_001,),
        ),
    )
    async with unit_of_work(sessions) as session:
        result = await session.exec(select_for_tenant(Repo, tenant_id))
        enabled_by_id = {repo.repo_id: repo.enabled for repo in result}
    assert enabled_by_id == {9_032_001: False, 9_032_002: True}
    async with unit_of_work(sessions) as session:
        other_repos = await session.exec(select_for_tenant(Repo, other_tenant_id))
        assert [repo.enabled for repo in other_repos] == [True]

    await on_installation_event(sessions, replace(event, action="suspend", repositories=()))
    with pytest.raises(InstallationSuspended):
        await get_tenant(sessions, tenant_id)

    await on_installation_event(sessions, replace(event, action="unsuspend", repositories=()))
    assert (await get_tenant(sessions, tenant_id)).installation_id == tenant_id
    async with unit_of_work(sessions) as session:
        result = await session.exec(select_for_tenant(Repo, tenant_id))
        enabled_by_id = {repo.repo_id: repo.enabled for repo in result}
    assert enabled_by_id == {9_032_001: False, 9_032_002: True}
