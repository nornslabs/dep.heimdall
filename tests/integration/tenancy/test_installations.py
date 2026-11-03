import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.tenancy.api import (
    get_tenant,
    on_installation_event,
    parse_installation_event,
)
from heimdall.modules.tenancy.models import Repo
from heimdall.shared.errors import InstallationNotFound, InstallationSuspended
from heimdall.shared.ids import InstallationId

pytestmark = pytest.mark.integration


def _fresh_id() -> int:
    return uuid.uuid4().int % 1_000_000_000 + 1


def _repo(repo_id: int, name: str) -> dict[str, object]:
    return {"id": repo_id, "full_name": f"octo-org/{name}", "name": name}


def _payload(
    installation_id: int, action: str, login: str = "octo-org", **extra: object
) -> dict[str, object]:
    return {
        "action": action,
        "installation": {
            "id": installation_id,
            "account": {"login": login, "type": "Organization"},
        },
        **extra,
    }


async def _apply(
    sessions: async_sessionmaker[AsyncSession], kind: str, payload: dict[str, object]
) -> None:
    event = parse_installation_event(kind, payload)
    assert event is not None
    await on_installation_event(sessions, event)


async def _installation(engine: AsyncEngine, installation_id: int) -> dict[str, object] | None:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT * FROM installations WHERE installation_id = :id"),
                {"id": installation_id},
            )
        ).first()
    return dict(row._mapping) if row is not None else None


async def _repos(engine: AsyncEngine, installation_id: int) -> dict[int, tuple[str, bool]]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT repo_id, full_name, enabled FROM repos WHERE installation_id = :id"),
            {"id": installation_id},
        )
        return {row.repo_id: (row.full_name, row.enabled) for row in rows}


async def test_created_installation_becomes_a_tenant_with_its_repos(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    inst, a, b = _fresh_id(), _fresh_id(), _fresh_id()
    repos = [_repo(a, "a"), _repo(b, "b")]
    await _apply(sessions, "installation", _payload(inst, "created", repositories=repos))

    ctx = await get_tenant(sessions, InstallationId(inst))

    assert ctx.installation_id == inst
    assert await _repos(engine, inst) == {a: ("octo-org/a", True), b: ("octo-org/b", True)}


async def test_redelivered_created_event_changes_nothing(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    inst, a = _fresh_id(), _fresh_id()
    payload = _payload(inst, "created", repositories=[_repo(a, "a")])
    await _apply(sessions, "installation", payload)
    before = (await _installation(engine, inst), await _repos(engine, inst))

    await _apply(sessions, "installation", payload)

    assert (await _installation(engine, inst), await _repos(engine, inst)) == before


async def test_suspended_installation_is_refused_by_get_tenant(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    inst = _fresh_id()
    await _apply(sessions, "installation", _payload(inst, "created"))
    await _apply(sessions, "installation", _payload(inst, "suspend"))

    with pytest.raises(InstallationSuspended):
        await get_tenant(sessions, InstallationId(inst))


async def test_unsuspended_installation_is_a_tenant_again(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    inst = _fresh_id()
    await _apply(sessions, "installation", _payload(inst, "created"))
    await _apply(sessions, "installation", _payload(inst, "suspend"))
    await _apply(sessions, "installation", _payload(inst, "unsuspend"))

    assert (await get_tenant(sessions, InstallationId(inst))).installation_id == inst


async def test_deleted_installation_is_suspended_not_removed(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    inst, a = _fresh_id(), _fresh_id()
    await _apply(sessions, "installation", _payload(inst, "created", repositories=[_repo(a, "a")]))

    await _apply(sessions, "installation", _payload(inst, "deleted"))

    installation = await _installation(engine, inst)
    assert installation is not None
    assert installation["suspended"] is True
    assert a in await _repos(engine, inst)
    with pytest.raises(InstallationSuspended):
        await get_tenant(sessions, InstallationId(inst))


async def test_removed_repo_is_disabled_and_readded_repo_is_enabled(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    inst, a = _fresh_id(), _fresh_id()
    await _apply(sessions, "installation", _payload(inst, "created", repositories=[_repo(a, "a")]))

    await _apply(
        sessions,
        "installation_repositories",
        _payload(inst, "removed", repositories_added=[], repositories_removed=[_repo(a, "a")]),
    )
    assert await _repos(engine, inst) == {a: ("octo-org/a", False)}

    renamed = [_repo(a, "a-renamed")]
    await _apply(
        sessions,
        "installation_repositories",
        _payload(inst, "added", repositories_added=renamed, repositories_removed=[]),
    )
    assert await _repos(engine, inst) == {a: ("octo-org/a-renamed", True)}


async def test_removing_an_unknown_repo_does_nothing(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    inst = _fresh_id()
    await _apply(sessions, "installation", _payload(inst, "created"))

    await _apply(
        sessions,
        "installation_repositories",
        _payload(inst, "removed", repositories_added=[], repositories_removed=[_repo(9, "x")]),
    )

    assert await _repos(engine, inst) == {}


async def test_account_rename_updates_the_login(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    inst = _fresh_id()
    await _apply(sessions, "installation", _payload(inst, "created"))

    await _apply(
        sessions, "installation", _payload(inst, "new_permissions_accepted", login="octo-new")
    )

    installation = await _installation(engine, inst)
    assert installation is not None
    assert installation["account_login"] == "octo-new"


async def test_repositories_event_for_unseen_installation_creates_it(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    inst, a = _fresh_id(), _fresh_id()

    await _apply(
        sessions,
        "installation_repositories",
        _payload(inst, "added", repositories_added=[_repo(a, "a")], repositories_removed=[]),
    )

    assert await _installation(engine, inst) is not None
    assert await _repos(engine, inst) == {a: ("octo-org/a", True)}


async def test_unknown_installation_raises_not_found(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(InstallationNotFound):
        await get_tenant(sessions, InstallationId(_fresh_id()))


async def test_tenant_isolation_each_installation_sees_only_its_repos(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    first, second, a, b = _fresh_id(), _fresh_id(), _fresh_id(), _fresh_id()
    await _apply(sessions, "installation", _payload(first, "created", repositories=[_repo(a, "a")]))
    await _apply(
        sessions, "installation", _payload(second, "created", repositories=[_repo(b, "b")])
    )

    async with unit_of_work(sessions) as session:
        rows = await session.exec(select_for_tenant(Repo, InstallationId(first)))
        visible = {repo.repo_id for repo in rows}

    assert visible == {a}


async def test_get_tenant_uses_given_request_id(sessions: async_sessionmaker[AsyncSession]) -> None:
    inst = _fresh_id()
    await _apply(sessions, "installation", _payload(inst, "created"))

    with_id = await get_tenant(sessions, InstallationId(inst), request_id="delivery-123")
    without_id = await get_tenant(sessions, InstallationId(inst))

    assert with_id.request_id == "delivery-123"
    assert without_id.request_id
