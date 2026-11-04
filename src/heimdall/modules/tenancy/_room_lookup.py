"""Find the tenant behind a Switch room, for messages people send to an agent.

Every other tenancy query starts from a tenant. A message in a room starts from the room, so
these lookups are the one place that reads ``repos`` across tenants. They return ids only,
never repo content, and refuse a room that would need a guess about whose it is.
"""

from dataclasses import dataclass
from typing import cast

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.modules.tenancy.models import Repo
from heimdall.shared.errors import RoomTenantAmbiguous
from heimdall.shared.ids import InstallationId, RepoId


@dataclass(frozen=True)
class RoomMaintainer:
    """A person who is a maintainer of a repo linked to a room.

    Attributes:
        installation_id: Tenant that owns the repo.
        repo_id: A repo linked to the room that names this person as a maintainer.
    """

    installation_id: InstallationId
    repo_id: RepoId


async def find_room_maintainer(
    session: AsyncSession, agent_room_id: str, login: str
) -> RoomMaintainer | None:
    """Find which tenant a sender speaks for in a room, if they are a maintainer there.

    Maintainers come from the config cached on each repo when intake last loaded it, so a repo
    whose config was never loaded names nobody. Matching ignores case.

    Args:
        session: A session inside the caller's transaction.
        agent_room_id: Switch room UUID the message arrived in.
        login: The sender's name, which for a Mattermost user is their GitHub login.

    Returns:
        The tenant and one of its repos in that room, or None when the room is unlinked or the
        sender is not a maintainer of any enabled repo linked to it.

    Raises:
        RoomTenantAmbiguous: the sender is a maintainer in repos of more than one installation
            sharing the room, so there is no safe tenant to act for.
    """
    statement = select(Repo).where(
        Repo.switch_agent_room_id == agent_room_id, col(Repo.enabled).is_(True)
    )
    repos = (await session.exec(statement)).all()
    wanted = login.casefold()
    matches = [repo for repo in repos if wanted in _maintainers(repo)]
    if not matches:
        return None
    installations = {repo.installation_id for repo in matches}
    if len(installations) > 1:
        raise RoomTenantAmbiguous("room is shared by installations that all name this sender")
    first = min(matches, key=lambda repo: repo.repo_id)
    return RoomMaintainer(InstallationId(first.installation_id), RepoId(first.repo_id))


async def find_room_tenant(session: AsyncSession, agent_room_id: str) -> InstallationId | None:
    """Find which tenant owns a room, so an agent can answer someone who is not a maintainer.

    Unlike ``find_room_maintainer`` this does not look at who is asking, so it must only be used
    for fixed replies that reveal nothing about the repo.

    Args:
        session: A session inside the caller's transaction.
        agent_room_id: Switch room UUID the message arrived in.

    Returns:
        The installation whose enabled repos are linked to the room, or None when no enabled
        repo links it.

    Raises:
        RoomTenantAmbiguous: repos of more than one installation share the room, so there is
            no safe tenant to act for.
    """
    statement = (
        select(col(Repo.installation_id))
        .where(Repo.switch_agent_room_id == agent_room_id, col(Repo.enabled).is_(True))
        .distinct()
    )
    installations = (await session.exec(statement)).all()
    if not installations:
        return None
    if len(installations) > 1:
        raise RoomTenantAmbiguous("room is shared by more than one installation")
    return InstallationId(installations[0])


async def linked_agent_room_ids(session: AsyncSession) -> tuple[str, ...]:
    """List the Switch rooms any enabled repo links for its agents, each once.

    Args:
        session: A session inside the caller's transaction.

    Returns:
        Room UUIDs in a stable order, for an agent to claim when it connects.
    """
    statement = (
        select(col(Repo.switch_agent_room_id))
        .where(col(Repo.switch_agent_room_id).is_not(None), col(Repo.enabled).is_(True))
        .distinct()
    )
    rooms = (await session.exec(statement)).all()
    return tuple(sorted(room for room in rooms if room))


def _maintainers(repo: Repo) -> frozenset[str]:
    """Case-folded maintainer logins from the repo's cached config, empty if absent or odd."""
    raw: object = repo.config.get("maintainers")
    if not isinstance(raw, list):
        return frozenset()
    logins: set[str] = set()
    for item in cast(list[object], raw):
        if isinstance(item, str):
            logins.add(item.casefold())
    return frozenset(logins)
