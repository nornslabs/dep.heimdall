"""Reading a repository's maintainers so Heimdall can mention them on its card."""

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.tenancy.api import get_tenant, on_installation_event, repo_maintainers
from heimdall.shared.ids import InstallationId, RepoId
from tests.integration.db.installation_helpers import created_installation

pytestmark = pytest.mark.integration


def _fresh_id() -> int:
    return uuid.uuid4().int % 1_000_000_000 + 1


async def _repo(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine, config: object
) -> tuple[InstallationId, RepoId]:
    installation_id, repo_id = _fresh_id(), _fresh_id()
    await on_installation_event(
        sessions, created_installation(installation_id, "octo-org", repo_id, f"octo-org/r{repo_id}")
    )
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE repos SET config = CAST(:config AS jsonb) WHERE repo_id = :id"),
            {"config": json.dumps(config), "id": repo_id},
        )
    return InstallationId(installation_id), RepoId(repo_id)


async def _maintainers(
    sessions: async_sessionmaker[AsyncSession], installation_id: InstallationId, repo_id: RepoId
) -> tuple[str, ...]:
    ctx = await get_tenant(sessions, installation_id)
    async with unit_of_work(sessions) as session:
        return await repo_maintainers(session, ctx, repo_id)


async def test_the_configured_maintainers_are_returned_in_order(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    installation_id, repo_id = await _repo(sessions, engine, {"maintainers": ["alice", "Bob"]})

    assert await _maintainers(sessions, installation_id, repo_id) == ("alice", "Bob")


async def test_a_repo_with_no_maintainers_returns_nothing(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    installation_id, repo_id = await _repo(sessions, engine, {})

    assert await _maintainers(sessions, installation_id, repo_id) == ()


async def test_entries_that_are_not_plain_logins_are_dropped(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    """A login with spaces, an @ or markup must not reach a room post as a mention."""
    config = {"maintainers": ["alice", 5, "bob smith", "@carol", "dave\n@everyone", "", "eve-1"]}
    installation_id, repo_id = await _repo(sessions, engine, config)

    assert await _maintainers(sessions, installation_id, repo_id) == ("alice", "eve-1")


async def test_another_installation_sees_no_maintainers(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    installation_id, repo_id = await _repo(sessions, engine, {"maintainers": ["alice"]})
    other, _ = await _repo(sessions, engine, {})
    assert installation_id != other

    assert await _maintainers(sessions, other, repo_id) == ()
