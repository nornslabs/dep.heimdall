import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.modules.tenancy.api import load_config
from heimdall.shared.context import TenantCtx
from heimdall.shared.errors import ConfigInvalid, GitHubUnavailable, RepoNotEnabled
from heimdall.shared.github import RepoRef
from heimdall.shared.ids import InstallationId, RepoId
from tests.integration.tenancy.fake_files import FakeConfigFiles

pytestmark = pytest.mark.integration

HEAD = "head123"
ORG_REPO = "octo-org/.github"


async def _seed_repo(engine: AsyncEngine) -> tuple[TenantCtx, RepoId, str]:
    """Insert an installation and a repo with fresh ids, so tests never share rows."""
    installation_id = uuid.uuid4().int % 1_000_000_000 + 1
    repo_id = uuid.uuid4().int % 1_000_000_000_000 + 1
    full_name = f"octo-org/demo-{repo_id}"
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO installations (installation_id, account_login, account_type) "
                "VALUES (:id, 'octo-org', 'Organization')"
            ),
            {"id": installation_id},
        )
        await conn.execute(
            text(
                "INSERT INTO repos (repo_id, installation_id, full_name) "
                "VALUES (:repo_id, :installation_id, :full_name)"
            ),
            {"repo_id": repo_id, "installation_id": installation_id, "full_name": full_name},
        )
    ctx = TenantCtx(installation_id=InstallationId(installation_id), request_id="req")
    return ctx, RepoId(repo_id), full_name


async def _stored(engine: AsyncEngine, repo_id: RepoId) -> tuple[str | None, dict[str, object]]:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT config_sha, config FROM repos WHERE repo_id = :id"), {"id": repo_id}
            )
        ).one()
    return row.config_sha, row.config


async def test_repo_file_overrides_org_file_overrides_defaults(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ctx, _, full_name = await _seed_repo(engine)
    files = FakeConfigFiles()
    files.files[(ORG_REPO, "heimdall.yml", None)] = (
        "maintainers: [ravi]\nintake:\n  require_cla: true\n"
    )
    files.files[(full_name, "heimdall.yml", HEAD)] = "maintainers: [meera]\n"

    config = await load_config(sessions, files, ctx, RepoRef(ctx.installation_id, full_name), HEAD)

    assert config.maintainers == ("meera",)
    assert config.intake.require_cla is True
    assert config.intake.require_assignee is True
    read_refs = {(repo.full_name, ref) for repo, _path, ref in files.reads}
    assert read_refs == {(ORG_REPO, None), (full_name, HEAD)}


async def test_missing_files_give_the_defaults(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ctx, _, full_name = await _seed_repo(engine)

    config = await load_config(
        sessions, FakeConfigFiles(), ctx, RepoRef(ctx.installation_id, full_name), HEAD
    )

    assert config.maintainers == ()
    assert config.review.max_findings == 5


async def test_config_and_sha_are_stored_on_the_repo(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ctx, repo_id, full_name = await _seed_repo(engine)
    files = FakeConfigFiles({(full_name, "heimdall.yml", HEAD): "maintainers: [asha]\n"})

    await load_config(sessions, files, ctx, RepoRef(ctx.installation_id, full_name), HEAD)

    config_sha, config = await _stored(engine, repo_id)
    assert config_sha is not None
    assert len(config_sha) == 64
    assert config["maintainers"] == ["asha"]


async def test_same_files_return_the_stored_config_without_rebuilding(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ctx, repo_id, full_name = await _seed_repo(engine)
    same_text = "maintainers: [asha]\n"
    files = FakeConfigFiles(
        {
            (full_name, "heimdall.yml", HEAD): same_text,
            (full_name, "heimdall.yml", "another-commit-same-files"): same_text,
        }
    )
    await load_config(sessions, files, ctx, RepoRef(ctx.installation_id, full_name), HEAD)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE repos SET config = jsonb_set(config, '{maintainers}', '[\"cached\"]') "
                "WHERE repo_id = :id"
            ),
            {"id": repo_id},
        )

    config = await load_config(
        sessions, files, ctx, RepoRef(ctx.installation_id, full_name), "another-commit-same-files"
    )

    assert config.maintainers == ("cached",)


async def test_changed_file_replaces_the_stored_config(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ctx, repo_id, full_name = await _seed_repo(engine)
    files = FakeConfigFiles({(full_name, "heimdall.yml", HEAD): "maintainers: [asha]\n"})
    await load_config(sessions, files, ctx, RepoRef(ctx.installation_id, full_name), HEAD)
    first_sha, _ = await _stored(engine, repo_id)
    files.files[(full_name, "heimdall.yml", "next")] = "maintainers: [ravi]\n"

    config = await load_config(
        sessions, files, ctx, RepoRef(ctx.installation_id, full_name), "next"
    )

    second_sha, stored = await _stored(engine, repo_id)
    assert config.maintainers == ("ravi",)
    assert stored["maintainers"] == ["ravi"]
    assert second_sha != first_sha


async def test_stored_config_that_no_longer_validates_is_rebuilt(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ctx, repo_id, full_name = await _seed_repo(engine)
    files = FakeConfigFiles({(full_name, "heimdall.yml", HEAD): "maintainers: [asha]\n"})
    await load_config(sessions, files, ctx, RepoRef(ctx.installation_id, full_name), HEAD)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE repos SET config = '{\"retired_key\": 1}' WHERE repo_id = :id"),
            {"id": repo_id},
        )

    config = await load_config(sessions, files, ctx, RepoRef(ctx.installation_id, full_name), HEAD)

    assert config.maintainers == ("asha",)
    assert (await _stored(engine, repo_id))[1]["maintainers"] == ["asha"]


async def test_invalid_config_raises_exact_error_and_stores_nothing(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ctx, repo_id, full_name = await _seed_repo(engine)
    files = FakeConfigFiles({(full_name, "heimdall.yml", HEAD): "intake:\n  require_clas: true\n"})

    with pytest.raises(ConfigInvalid) as raised:
        await load_config(sessions, files, ctx, RepoRef(ctx.installation_id, full_name), HEAD)

    assert str(raised.value) == (
        "heimdall.yml is invalid:\n- intake.require_clas: unknown key (did you mean 'require_cla'?)"
    )
    assert await _stored(engine, repo_id) == (None, {})


async def test_invalid_org_file_names_the_org_repo(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ctx, _, full_name = await _seed_repo(engine)
    files = FakeConfigFiles({(ORG_REPO, "heimdall.yml", None): "colour: blue\n"})

    with pytest.raises(ConfigInvalid) as raised:
        await load_config(sessions, files, ctx, RepoRef(ctx.installation_id, full_name), HEAD)

    assert str(raised.value) == "octo-org/.github: heimdall.yml is invalid:\n- colour: unknown key"


async def test_other_tenants_repo_is_not_found(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    _, _, full_name = await _seed_repo(engine)
    other_ctx, _, _ = await _seed_repo(engine)
    files = FakeConfigFiles()

    with pytest.raises(RepoNotEnabled):
        await load_config(
            sessions, files, other_ctx, RepoRef(other_ctx.installation_id, full_name), HEAD
        )

    assert files.reads == []


async def test_github_outage_propagates_as_retryable(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ctx, repo_id, full_name = await _seed_repo(engine)
    files = FakeConfigFiles(error=GitHubUnavailable("502"))

    with pytest.raises(GitHubUnavailable) as raised:
        await load_config(sessions, files, ctx, RepoRef(ctx.installation_id, full_name), HEAD)

    assert raised.value.retryable is True
    assert await _stored(engine, repo_id) == (None, {})
