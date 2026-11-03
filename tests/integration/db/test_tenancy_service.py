"""Integration tests for tenant and repository lifecycle handling."""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.tenancy.api import (
    InstallationEvent,
    get_tenant,
    on_installation_event,
    parse_installation_event,
)
from heimdall.modules.tenancy.models import Repo
from heimdall.shared.errors import InstallationSuspended
from heimdall.shared.ids import InstallationId

pytestmark = pytest.mark.integration


def _event(
    kind: str,
    action: str,
    installation_id: int,
    login: str,
    **repositories: object,
) -> InstallationEvent:
    payload: dict[str, object] = {
        "action": action,
        "installation": {
            "id": installation_id,
            "account": {"login": login, "type": "Organization"},
        },
        **repositories,
    }
    event = parse_installation_event(kind, payload)
    assert event is not None
    return event


async def test_installation_events_enable_disable_and_suspend_repos(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Repository access follows install, remove, suspend and unsuspend events."""
    tenant_id = InstallationId(9032)
    await on_installation_event(
        sessions,
        _event(
            "installation",
            "created",
            tenant_id,
            "octo-org",
            repositories=[
                {"id": 9_032_001, "full_name": "octo-org/one"},
                {"id": 9_032_002, "full_name": "octo-org/two"},
            ],
        ),
    )
    other_tenant_id = InstallationId(9033)
    await on_installation_event(
        sessions,
        _event(
            "installation",
            "created",
            other_tenant_id,
            "other-org",
            repositories=[{"id": 9_033_001, "full_name": "other-org/repo"}],
        ),
    )
    assert (await get_tenant(sessions, tenant_id)).installation_id == tenant_id

    await on_installation_event(
        sessions,
        _event(
            "installation_repositories",
            "removed",
            tenant_id,
            "octo-org",
            repositories_added=[],
            repositories_removed=[{"id": 9_032_001, "full_name": "octo-org/one"}],
        ),
    )
    async with unit_of_work(sessions) as session:
        result = await session.exec(select_for_tenant(Repo, tenant_id))
        enabled_by_id = {repo.repo_id: repo.enabled for repo in result}
    assert enabled_by_id == {9_032_001: False, 9_032_002: True}

    async with unit_of_work(sessions) as session:
        other_repos = await session.exec(select_for_tenant(Repo, other_tenant_id))
        assert [repo.enabled for repo in other_repos] == [True]

    await on_installation_event(sessions, _event("installation", "suspend", tenant_id, "octo-org"))
    with pytest.raises(InstallationSuspended):
        await get_tenant(sessions, tenant_id)
    await on_installation_event(
        sessions, _event("installation", "unsuspend", tenant_id, "octo-org")
    )
    assert (await get_tenant(sessions, tenant_id)).installation_id == tenant_id
    async with unit_of_work(sessions) as session:
        result = await session.exec(select_for_tenant(Repo, tenant_id))
        enabled_by_id = {repo.repo_id: repo.enabled for repo in result}
    assert enabled_by_id == {9_032_001: False, 9_032_002: True}
