"""Tenancy use cases: tenants from installation webhooks, and each repo's effective config."""

from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.tenancy._installation_store import apply_installation_event
from heimdall.modules.tenancy.domain import (
    CONFIG_PATH,
    ORG_CONFIG_REPO,
    ConfigFile,
    InstallationEvent,
    RepoConfig,
    build_config,
    config_sha,
    parse_layer,
)
from heimdall.modules.tenancy.models import Installation, Repo
from heimdall.modules.tenancy.ports import ConfigFileReader
from heimdall.shared.context import TenantCtx, new_request_id
from heimdall.shared.errors import InstallationNotFound, InstallationSuspended, RepoNotFound
from heimdall.shared.github import RepoRef
from heimdall.shared.ids import InstallationId, RepoId


@dataclass(frozen=True)
class TenancyDeps:
    """What tenancy use cases need, wired in ``apps/*/main.py``.

    Attributes:
        sessions: Session factory for Heimdall's database.
        files: Reads heimdall.yml from GitHub.
    """

    sessions: async_sessionmaker[AsyncSession]
    files: ConfigFileReader


async def on_installation_event(deps: TenancyDeps, event: InstallationEvent) -> None:
    """Create, update or suspend a tenant and its repos from a GitHub installation webhook.

    Safe to repeat: GitHub redelivers webhooks, and applying the same event twice leaves the
    rows as they were after the first time.

    Args:
        deps: Database access.
        event: From ``parse_installation_event``.
    """
    async with unit_of_work(deps.sessions) as session:
        await apply_installation_event(session, event)


async def get_tenant(
    deps: TenancyDeps, installation_id: InstallationId, request_id: str | None = None
) -> TenantCtx:
    """Start work for an installation: the context every use case takes.

    Args:
        deps: Database access.
        installation_id: The installation a webhook or job belongs to.
        request_id: Correlation id, such as the webhook delivery id; a new one if omitted.

    Returns:
        The tenant context.

    Raises:
        InstallationNotFound: Heimdall has never seen this installation.
        InstallationSuspended: it is suspended or uninstalled, so no work is done for it.
    """
    async with unit_of_work(deps.sessions) as session:
        installation = await session.get(Installation, installation_id)
    if installation is None:
        raise InstallationNotFound(f"installation {installation_id} is not known")
    if installation.suspended:
        raise InstallationSuspended(f"installation {installation_id} is suspended")
    return TenantCtx(installation_id=installation_id, request_id=request_id or new_request_id())


async def load_config(deps: TenancyDeps, ctx: TenantCtx, repo_id: RepoId, sha: str) -> RepoConfig:
    """Return a repo's effective config at a commit: defaults < org .github < the repo.

    The files are fetched every time (a push can edit heimdall.yml), but when they are the
    same as last time the stored config is reused instead of rebuilt and rewritten.

    Args:
        deps: Database and GitHub access.
        ctx: Tenant context; only this installation's repos are visible.
        repo_id: The repository the PR belongs to.
        sha: The PR's head commit; the repo's heimdall.yml is read there.

    Returns:
        The validated config.

    Raises:
        RepoNotFound: the repo is not enabled for this installation.
        ConfigInvalid: a file, or the merged config, is invalid; nothing is stored.
        GitHubUnavailable: GitHub is down, slow or rate limiting (retryable).
    """
    async with unit_of_work(deps.sessions) as session:
        repo = await _get_repo(session, ctx, repo_id)
        files = await _fetch_config_files(deps.files, ctx, repo.full_name, sha)
        fingerprint = config_sha(files)
        cached = _cached_config(repo, fingerprint)
        if cached is not None:
            return cached
        config = build_config([parse_layer(file.text, file.source) for file in files])
        repo.config = config.model_dump(mode="json")
        repo.config_sha = fingerprint
        session.add(repo)
        return config


async def _get_repo(session: AsyncSession, ctx: TenantCtx, repo_id: RepoId) -> Repo:
    statement = select_for_tenant(Repo, ctx.installation_id).where(col(Repo.repo_id) == repo_id)
    repo = (await session.exec(statement)).first()
    if repo is None:
        raise RepoNotFound(f"repo {repo_id} is not enabled for this installation")
    return repo


async def _fetch_config_files(
    files: ConfigFileReader, ctx: TenantCtx, full_name: str, sha: str
) -> list[ConfigFile]:
    """Fetch the org file (default branch) and the repo file (at ``sha``); skip missing ones.

    The org's ``.github`` repo has no copy of the PR's commit, so it is read at its default
    branch.
    """
    owner = full_name.split("/", 1)[0]
    org_repo = RepoRef(installation_id=ctx.installation_id, full_name=f"{owner}/{ORG_CONFIG_REPO}")
    pr_repo = RepoRef(installation_id=ctx.installation_id, full_name=full_name)
    found: list[ConfigFile] = []
    org_text = await files.get_file(org_repo, CONFIG_PATH, None)
    if org_text is not None:
        found.append(ConfigFile(source=f"{org_repo.full_name}: {CONFIG_PATH}", text=org_text))
    repo_text = await files.get_file(pr_repo, CONFIG_PATH, sha)
    if repo_text is not None:
        found.append(ConfigFile(source=CONFIG_PATH, text=repo_text))
    return found


def _cached_config(repo: Repo, fingerprint: str) -> RepoConfig | None:
    """The stored config if it was built from these exact files and still validates."""
    if repo.config_sha != fingerprint:
        return None
    try:
        return RepoConfig.model_validate(repo.config)
    except ValidationError:
        return None  # stored under an older schema: rebuild it
