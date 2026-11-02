import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration


async def _seed_repo(engine: AsyncEngine, installation_id: int, repo_id: int) -> None:
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "INSERT INTO installations (installation_id, account_login, account_type) "
            f"VALUES ({installation_id}, 'octo-org', 'Organization')"
        )
        await conn.exec_driver_sql(
            "INSERT INTO repos (repo_id, installation_id, full_name) "
            f"VALUES ({repo_id}, {installation_id}, 'octo-org/demo')"
        )


def _insert_pr(repo_id: int, installation_id: int) -> str:
    return (
        "INSERT INTO prs (id, installation_id, repo_id, number, author, head_sha, base_sha, "
        f"state) VALUES ('{uuid.uuid4()}', {installation_id}, {repo_id}, 12, 'ravi', 'abc', "
        "'def', 'received')"
    )


async def test_a_pr_number_is_unique_within_a_repo(engine: AsyncEngine) -> None:
    await _seed_repo(engine, installation_id=3001, repo_id=4_000_000_001)
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_insert_pr(4_000_000_001, 3001))

    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.exec_driver_sql(_insert_pr(4_000_000_001, 3001))


async def test_pr_needs_an_existing_repo(engine: AsyncEngine) -> None:
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.exec_driver_sql(_insert_pr(999, 3002))
