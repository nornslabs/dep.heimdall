"""Handle Switch reactions that approve an exact saved draft."""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.switch.ports import RoomEvent, RoomPort
from heimdall.modules.approval.api import pause_auto_suggestions, record_approval
from heimdall.modules.desk.api import CommandVerb, parse_command
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
AUTO_PAUSED_REPLY = "Automatic mechanical suggestions are paused for this repository."


@dataclass(frozen=True)
class ApprovalMarker:
    """The persisted draft identity and tenant details shown on an approval post."""

    draft_id: uuid.UUID
    draft_hash: str
    installation_id: InstallationId
    repo_full_name: str


def parse_approval_marker(message: str) -> ApprovalMarker | None:
    """Parse a complete approval marker from a Switch post.

    Args:
        message: Untrusted Switch post text.

    Returns:
        Parsed draft identity and tenant details, or ``None`` for invalid markers.
    """
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
        """Wire tenant services, repository config reading and Switch operations.

        Args:
            sessions: Database session factory for tenant-scoped operations.
            reader: GitHub config reader used to validate maintainer policy.
            room: Switch operations for resolving posts and replying in threads.
        """
        self._sessions = sessions
        self._reader = reader
        self._room = room

    async def handle(self, event: RoomEvent) -> None:
        """Process one approval reaction or maintainer pause command.

        Args:
            event: Untrusted Switch post or reaction event.
        """
        if event.kind == "posted":
            command = parse_command(event.message)
            if command is not None and command.verb == CommandVerb.PAUSE_AUTO:
                await self._pause_auto(event)
            return
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

    async def _pause_auto(self, event: RoomEvent) -> None:
        """Pause policy for the draft's repository after a maintainer command."""
        if not event.root_id:
            return
        post = await self._room.get_post(event.root_id)
        marker = parse_approval_marker(post.message)
        if marker is None:
            return
        approver = await self._room.get_username(event.user_id)
        ctx = await get_tenant(self._sessions, marker.installation_id)
        repo = RepoRef(ctx.installation_id, marker.repo_full_name)
        try:
            await pause_auto_suggestions(
                self._sessions,
                self._reader,
                ctx,
                repo,
                marker.draft_id,
                approver,
            )
        except NotAMaintainer:
            reply = NON_MAINTAINER_REPLY
        else:
            reply = AUTO_PAUSED_REPLY
        await self._room.post_to_thread(post.channel_id, post.id, reply)
