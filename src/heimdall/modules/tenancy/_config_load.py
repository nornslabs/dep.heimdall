"""Load layered repository config and cache it by the source file contents."""

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.tenancy.domain import (
    ORG_CONFIG_REPO,
    ConfigFile,
    RepoConfig,
    build_config,
    config_sha,
    parse_layer,
)
from heimdall.modules.tenancy.models import Repo
from heimdall.modules.tenancy.ports import ConfigFileReader
from heimdall.shared.context import TenantCtx
from heimdall.shared.errors import RepoNotEnabled
from heimdall.shared.github import RepoRef

REPO_CONFIG_PATH = "heimdall.yml"


async def load_config(
    sessions: async_sessionmaker[AsyncSession],
    reader: ConfigFileReader,
    ctx: TenantCtx,
    repo: RepoRef,
    sha: str,
) -> RepoConfig:
    """Load and cache the effective config for a repository head.

    Args:
        sessions: Tenant database session factory.
        reader: Reads organization and repository config files from GitHub.
        ctx: Tenant and request context.
        repo: Repository whose config is being loaded.
        sha: Pull request head containing the repository config.

    Returns:
        The merged and validated repository config.

    Raises:
        RepoNotEnabled: The repository is not enabled for this installation.
    """
    async with unit_of_work(sessions) as session:
        return await load_config_in_transaction(session, reader, ctx, repo, sha)


async def load_config_in_transaction(
    session: AsyncSession,
    reader: ConfigFileReader,
    ctx: TenantCtx,
    repo: RepoRef,
    sha: str,
) -> RepoConfig:
    """Load config while participating in the caller's transaction.

    Args:
        session: Session inside the caller's transaction.
        reader: Reads organization and repository config files from GitHub.
        ctx: Tenant and request context.
        repo: Repository whose config is being loaded.
        sha: Pull request head containing the repository config.

    Returns:
        The merged and validated repository config.

    Raises:
        RepoNotEnabled: The repository is not enabled for this installation.
    """
    if repo.installation_id != ctx.installation_id:
        raise RepoNotEnabled("repository belongs to another installation")
    record = await _repo_record(session, ctx, repo.full_name)
    if record.config_head_sha == sha and record.config_sha is not None:
        cached = _cached_config(record, record.config_sha)
        if cached is not None:
            return cached
    files = await _read_config_files(reader, repo, sha)
    fingerprint = config_sha(files)
    cached = _cached_config(record, fingerprint)
    if cached is not None:
        record.config_head_sha = sha
        session.add(record)
        return cached
    config = build_config([parse_layer(file.text, file.source) for file in files])
    record.config_head_sha = sha
    record.config_sha = fingerprint
    record.config = config.model_dump(mode="json")
    session.add(record)
    return config


async def _repo_record(session: AsyncSession, ctx: TenantCtx, full_name: str) -> Repo:
    """Lock the enabled tenant repository while updating its config cache."""
    statement = (
        select_for_tenant(Repo, ctx.installation_id)
        .where(Repo.full_name == full_name, col(Repo.enabled).is_(True))
        .with_for_update()
    )
    result = await session.exec(statement)
    record = result.first()
    if record is None:
        raise RepoNotEnabled(f"repository {full_name} is not enabled")
    return record


async def _read_config_files(reader: ConfigFileReader, repo: RepoRef, sha: str) -> list[ConfigFile]:
    """Read organization defaults and the repo override at the requested head."""
    owner = repo.full_name.split("/", maxsplit=1)[0]
    org_repo = RepoRef(repo.installation_id, f"{owner}/{ORG_CONFIG_REPO}")
    files: list[ConfigFile] = []
    org_text = await reader.get_file(org_repo, REPO_CONFIG_PATH, None)
    if org_text is not None:
        files.append(ConfigFile(source=f"{org_repo.full_name}: {REPO_CONFIG_PATH}", text=org_text))
    repo_text = await reader.get_file(repo, REPO_CONFIG_PATH, sha)
    if repo_text is not None:
        files.append(ConfigFile(source=REPO_CONFIG_PATH, text=repo_text))
    return files


def _cached_config(record: Repo, fingerprint: str) -> RepoConfig | None:
    """Return a matching valid cache entry, rebuilding data saved under an old schema."""
    if record.config_sha != fingerprint:
        return None
    try:
        return RepoConfig.model_validate(record.config)
    except ValidationError:
        return None
