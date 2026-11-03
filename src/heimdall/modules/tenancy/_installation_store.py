"""Writes for installation events: idempotent upserts, so a redelivered webhook changes nothing."""

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import col
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.modules.tenancy._installation_events import (
    InstallationAction,
    InstallationEvent,
    RepoSummary,
)
from heimdall.modules.tenancy.models import Installation, Repo
from heimdall.shared.ids import InstallationId, RepoId


async def apply_installation_event(session: AsyncSession, event: InstallationEvent) -> None:
    """Apply one installation event inside the caller's transaction.

    The installation row is upserted first on every event, so a repositories event for an
    installation Heimdall missed still has a tenant to attach to.

    Args:
        session: A session inside ``unit_of_work``.
        event: The parsed webhook.
    """
    await _upsert_installation(session, event)
    for repo in event.repos_enabled:
        await _enable_repo(session, event.installation_id, repo)
    if event.repos_disabled:
        await _disable_repos(session, event.installation_id, event.repos_disabled)


async def _upsert_installation(session: AsyncSession, event: InstallationEvent) -> None:
    """Create the installation or refresh its account; change ``suspended`` only when asked."""
    changes: dict[str, object] = {
        "account_login": event.account_login,
        "account_type": event.account_type,
    }
    if event.action is not InstallationAction.UPSERT:
        changes["suspended"] = event.action is InstallationAction.SUSPEND
    new_row = {"installation_id": event.installation_id, "suspended": False, **changes}
    statement = (
        insert(Installation)
        .values(**new_row)
        .on_conflict_do_update(index_elements=["installation_id"], set_=changes)
    )
    await session.exec(statement)


async def _enable_repo(
    session: AsyncSession, installation_id: InstallationId, repo: RepoSummary
) -> None:
    """Create the repo, or re-enable it and refresh its name (repos can be renamed)."""
    changes = {"full_name": repo.full_name, "enabled": True, "installation_id": installation_id}
    statement = (
        insert(Repo)
        .values(repo_id=repo.repo_id, **changes)
        .on_conflict_do_update(index_elements=["repo_id"], set_=changes)
    )
    await session.exec(statement)


async def _disable_repos(
    session: AsyncSession, installation_id: InstallationId, repo_ids: tuple[RepoId, ...]
) -> None:
    """Switch repos off; ids Heimdall never saw (or another tenant's) match nothing."""
    statement = (
        update(Repo)
        .where(col(Repo.installation_id) == installation_id)
        .where(col(Repo.repo_id).in_(repo_ids))
        .values(enabled=False)
    )
    await session.exec(statement)
