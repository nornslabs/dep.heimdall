import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import Field
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import TenantScoped, select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.shared.ids import InstallationId

pytestmark = pytest.mark.integration


class Widget(TenantScoped, table=True):
    __tablename__ = "test_widgets"  # pyright: ignore[reportAssignmentType]

    id: int | None = Field(default=None, primary_key=True)
    name: str


class BoomError(Exception):
    pass


async def _names(sessions: async_sessionmaker[AsyncSession], tenant: int) -> list[str]:
    async with unit_of_work(sessions) as session:
        rows = await session.exec(select_for_tenant(Widget, InstallationId(tenant)))
        return [row.name for row in rows]


async def test_unit_of_work_commits_on_success(sessions: async_sessionmaker[AsyncSession]) -> None:
    name = uuid.uuid4().hex
    async with unit_of_work(sessions) as session:
        session.add(Widget(installation_id=101, name=name))

    assert name in await _names(sessions, 101)


async def test_unit_of_work_rolls_back_everything_on_error(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(BoomError):
        async with unit_of_work(sessions) as session:
            session.add(Widget(installation_id=102, name="first"))
            await session.flush()
            session.add(Widget(installation_id=102, name="second"))
            raise BoomError

    assert await _names(sessions, 102) == []


async def test_tenant_filter_hides_other_installations_rows(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(sessions) as session:
        session.add(Widget(installation_id=201, name="mine"))
        session.add(Widget(installation_id=202, name="theirs"))

    assert await _names(sessions, 201) == ["mine"]
