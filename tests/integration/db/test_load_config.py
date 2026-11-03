"""Integration tests for tenant config loading and cache persistence."""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.tenancy.models import Installation, Repo
from heimdall.modules.tenancy.service import load_config
from heimdall.shared.context import TenantCtx
from heimdall.shared.github import RepoRef
from heimdall.shared.ids import InstallationId

pytestmark = pytest.mark.integration


class RecordingConfigReader:
    """Return configured GitHub files and record requested paths."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    async def get_file(self, repo: RepoRef, path: str, ref: str | None) -> str | None:
        self.calls.append((repo.full_name, path, ref))
        return {
            ("octo-org/.github", "heimdall.yml", None): "intake:\n  require_cla: true\n",
            ("octo-org/demo", "heimdall.yml", "head-sha"): "intake:\n  required_labels: [bug]\n",
        }.get((repo.full_name, path, ref))


async def test_load_config_merges_layers_and_reuses_cached_sha(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Org defaults merge with repo settings and the same SHA avoids refetches."""
    installation_id = 9031
    async with unit_of_work(sessions) as session:
        session.add(
            Installation(
                installation_id=installation_id,
                account_login="octo-org",
                account_type="Organization",
            )
        )
        session.add(
            Repo(
                repo_id=9_031_001,
                installation_id=installation_id,
                full_name="octo-org/demo",
                config={},
            )
        )
    ctx = TenantCtx(installation_id=InstallationId(installation_id), request_id="config-test")
    repo = RepoRef(installation_id=InstallationId(installation_id), full_name="octo-org/demo")
    reader = RecordingConfigReader()

    config = await load_config(sessions, reader, ctx, repo, "head-sha")
    cached = await load_config(sessions, reader, ctx, repo, "head-sha")

    assert config.intake.require_cla is True
    assert config.intake.required_labels == ("bug",)
    assert cached == config
    assert reader.calls == [
        ("octo-org/.github", "heimdall.yml", None),
        ("octo-org/demo", "heimdall.yml", "head-sha"),
    ]
