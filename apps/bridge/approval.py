"""Handle Switch reactions that approve an exact saved draft."""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.switch.ports import RoomEvent, RoomPort
from heimdall.modules.approval.api import record_approval
from heimdall.modules.tenancy.api import ConfigFileReader, get_tenant
from heimdall.shared.errors import NotAMaintainer
from heimdall.shared.github import RepoRef
from heimdall.shared.ids import InstallationId

APPROVAL_MARKER = re.compile(
    r"<!-- heimdall-approval "
    r"draft_id=(?P<draft_id>[0-9a-f-]{36}) "
    r"draft_hash=(?P<draft_hash>[0-9a-f]{64}) "
    r"installation_id=(?P<installation_id>[1-9][0-9]*) "
    r"repo=(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+) -->"
)
THUMBS_UP = frozenset({"+1", "thumbsup", "thumbs_up"})
NON_MAINTAINER_REPLY = "Only a configured repository maintainer can approve this draft."


@dataclass(frozen=True)
class ApprovalMarker:
    """The persisted draft identity and tenant details shown on an approval post."""

    draft_id: uuid.UUID
    draft_hash: str
    installation_id: InstallationId
    repo_full_name: str


def parse_approval_marker(message: str) -> ApprovalMarker | None:
    """Parse a complete approval marker from a Switch post."""
    match = APPROVAL_MARKER.search(message)
    if match is None:
        return None
    try:
        draft_id = uuid.UUID(match.group("draft_id"))
        installation_id = int(match.group("installation_id"))
    except ValueError:
        return None
    return ApprovalMarker(
        draft_id=draft_id,
        draft_hash=match.group("draft_hash"),
        installation_id=InstallationId(installation_id),
        repo_full_name=match.group("repo"),
    )


class ApprovalReactionHandler:
    """Record configured maintainers' thumbs-up reactions on marked draft posts."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        reader: ConfigFileReader,
        room: RoomPort,
    ) -> None:
        """Wire tenant services, repository config reading and Switch operations."""
        self._sessions = sessions
        self._reader = reader
        self._room = room

    async def handle(self, event: RoomEvent) -> None:
        """Approve the marked draft or reply when the reactor is not a maintainer."""
        if event.kind != "reaction_added" or event.emoji_name not in THUMBS_UP:
            return
        post = await self._room.get_post(event.post_id)
        marker = parse_approval_marker(post.message)
        if marker is None:
            return
        approver = await self._room.get_username(event.user_id)
        ctx = await get_tenant(self._sessions, marker.installation_id)
        repo = RepoRef(ctx.installation_id, marker.repo_full_name)
        try:
            await record_approval(
                self._sessions,
                self._reader,
                ctx,
                repo,
                marker.draft_id,
                approver,
                marker.draft_hash,
            )
        except NotAMaintainer:
            root_id = post.root_id or post.id
            await self._room.post_to_thread(post.channel_id, root_id, NON_MAINTAINER_REPLY)
