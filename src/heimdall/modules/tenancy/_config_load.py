"""Load layered config and cache it against an installation's repository head."""

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.tenancy.domain import ConfigLayer, RepoConfig, build_config, parse_layer
from heimdall.modules.tenancy.models import Repo
from heimdall.modules.tenancy.ports import ConfigFileReader
from heimdall.shared.context import TenantCtx
from heimdall.shared.errors import RepoNotEnabled
from heimdall.shared.github import RepoRef

ORG_CONFIG_PATH = ".github/heimdall.yml"
REPO_CONFIG_PATH = "heimdall.yml"


async def load_config(
    sessions: async_sessionmaker[AsyncSession],
    reader: ConfigFileReader,
    ctx: TenantCtx,
    repo: RepoRef,
    sha: str,
) -> RepoConfig:
    """Load and cache the effective config for one repository head.

    Args:
        sessions: Tenant database session factory.
        reader: GitHub reader used to fetch the two config files.
        ctx: Tenant and request context.
        repo: Repository whose config is loaded.
        sha: Commit SHA containing the config files.

    Returns:
        Merged, validated repository config.

    Raises:
        RepoNotEnabled: Repository is not registered and enabled for this installation.
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
        reader: GitHub reader used to fetch config files.
        ctx: Tenant and request context.
        repo: Repository whose config is loaded.
        sha: Commit SHA containing the config files.

    Returns:
        Merged, validated repository config.

    Raises:
        RepoNotEnabled: Repository is not registered and enabled for this installation.
    """
    if repo.installation_id != ctx.installation_id:
        raise RepoNotEnabled("repository belongs to another installation")
    record = await _repo_record(session, ctx, repo.full_name)
    if record.config_sha == sha:
        return RepoConfig.model_validate(record.config)
    config = build_config(await _read_layers(reader, repo, sha))
    record.config_sha = sha
    record.config = config.model_dump(mode="json")
    session.add(record)
    return config


async def _repo_record(session: AsyncSession, ctx: TenantCtx, full_name: str) -> Repo:
    """Lock the repo row inside the installation before updating its config cache."""
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


async def _read_layers(reader: ConfigFileReader, repo: RepoRef, sha: str) -> list[ConfigLayer]:
    """Fetch organization defaults before repository overrides at the same SHA."""
    org_text = await reader.get_file(repo, ORG_CONFIG_PATH, sha)
    repo_text = await reader.get_file(repo, REPO_CONFIG_PATH, sha)
    layers: list[ConfigLayer] = []
    if org_text is not None:
        layers.append(parse_layer(org_text, ORG_CONFIG_PATH))
    if repo_text is not None:
        layers.append(parse_layer(repo_text, REPO_CONFIG_PATH))
    return layers
