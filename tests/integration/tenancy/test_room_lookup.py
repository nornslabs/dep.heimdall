"""Resolving the tenant behind a Switch room and a sender, for messages sent to an agent."""

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.tenancy.api import (
    find_room_maintainer,
    get_tenant,
    link_repo_room,
    linked_agent_room_ids,
    on_installation_event,
)
from heimdall.shared.errors import RoomTenantAmbiguous
from heimdall.shared.ids import InstallationId, RepoId
from tests.integration.db.installation_helpers import created_installation

pytestmark = pytest.mark.integration


def _fresh_id() -> int:
    return uuid.uuid4().int % 1_000_000_000 + 1


async def _set_maintainers(engine: AsyncEngine, repo_id: int, maintainers: list[str]) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE repos SET config = CAST(:config AS jsonb) WHERE repo_id = :id"),
            {"config": json.dumps({"maintainers": maintainers}), "id": repo_id},
        )


async def _repo_in_room(
    sessions: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    room: str,
    maintainers: list[str] | None,
) -> tuple[int, int]:
    """Install one repo, link its agent room and cache its config; return (installation, repo)."""
    installation_id, repo_id = _fresh_id(), _fresh_id()
    event = created_installation(installation_id, "octo-org", repo_id, f"octo-org/r{repo_id}")
    await on_installation_event(sessions, event)
    ctx = await get_tenant(sessions, InstallationId(installation_id))
    await link_repo_room(sessions, ctx, RepoId(repo_id), "mm-channel", agent_room_id=room)
    if maintainers is not None:
        await _set_maintainers(engine, repo_id, maintainers)
    return installation_id, repo_id


async def _find(
    sessions: async_sessionmaker[AsyncSession], room: str, login: str
) -> tuple[int, int] | None:
    async with unit_of_work(sessions) as session:
        found = await find_room_maintainer(session, room, login)
    return None if found is None else (found.installation_id, found.repo_id)


async def test_a_maintainer_of_a_repo_in_the_room_is_resolved_to_that_repos_tenant(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room = f"room-{uuid.uuid4().hex}"
    expected = await _repo_in_room(sessions, engine, room, ["alice", "bob"])

    assert await _find(sessions, room, "bob") == expected


@pytest.mark.parametrize("login", ["alice", "ALICE", "Alice"])
async def test_the_match_ignores_case(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine, login: str
) -> None:
    room = f"room-{uuid.uuid4().hex}"
    expected = await _repo_in_room(sessions, engine, room, ["Alice"])

    assert await _find(sessions, room, login) == expected


async def test_someone_who_is_not_a_maintainer_resolves_to_nothing(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room = f"room-{uuid.uuid4().hex}"
    await _repo_in_room(sessions, engine, room, ["alice"])

    assert await _find(sessions, room, "mallory") is None


async def test_a_repo_whose_config_was_never_loaded_has_no_maintainers(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room = f"room-{uuid.uuid4().hex}"
    await _repo_in_room(sessions, engine, room, None)

    assert await _find(sessions, room, "alice") is None


async def test_an_unlinked_room_resolves_to_nothing(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    assert await _find(sessions, f"room-{uuid.uuid4().hex}", "alice") is None


async def test_a_disabled_repo_no_longer_counts(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room = f"room-{uuid.uuid4().hex}"
    _, repo_id = await _repo_in_room(sessions, engine, room, ["alice"])
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE repos SET enabled = false WHERE repo_id = :id"), {"id": repo_id}
        )

    assert await _find(sessions, room, "alice") is None


async def test_one_person_maintaining_in_two_installations_of_a_room_is_refused(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room = f"room-{uuid.uuid4().hex}"
    await _repo_in_room(sessions, engine, room, ["alice"])
    await _repo_in_room(sessions, engine, room, ["alice"])

    with pytest.raises(RoomTenantAmbiguous):
        await _find(sessions, room, "alice")


async def test_two_repos_of_one_installation_in_a_room_are_not_ambiguous(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room = f"room-{uuid.uuid4().hex}"
    installation_id, first_repo = await _repo_in_room(sessions, engine, room, ["alice"])
    second_repo = _fresh_id()
    await on_installation_event(
        sessions,
        created_installation(installation_id, "octo-org", second_repo, f"octo-org/r{second_repo}"),
    )
    ctx = await get_tenant(sessions, InstallationId(installation_id))
    await link_repo_room(sessions, ctx, RepoId(second_repo), "mm-channel", agent_room_id=room)
    await _set_maintainers(engine, second_repo, ["alice"])

    found = await _find(sessions, room, "alice")

    assert found is not None
    assert found[0] == installation_id
    assert found[1] in {first_repo, second_repo}


async def test_linked_agent_rooms_lists_each_enabled_repos_room_once(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room = f"room-{uuid.uuid4().hex}"
    disabled_room = f"room-{uuid.uuid4().hex}"
    await _repo_in_room(sessions, engine, room, None)
    await _repo_in_room(sessions, engine, room, None)
    _, disabled_repo = await _repo_in_room(sessions, engine, disabled_room, None)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE repos SET enabled = false WHERE repo_id = :id"), {"id": disabled_repo}
        )

    async with unit_of_work(sessions) as session:
        rooms = await linked_agent_room_ids(session)

    assert rooms.count(room) == 1
    assert disabled_room not in rooms
