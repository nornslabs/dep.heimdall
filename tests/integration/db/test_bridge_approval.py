"""Integration coverage for Switch approval reactions and tenant services."""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from apps.bridge.approval import (
    AUTO_PAUSED_REPLY,
    NON_MAINTAINER_REPLY,
    ApprovalReactionHandler,
)
from heimdall.adapters.db.outbox.models import OutboxMessage
from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.adapters.switch.ports import RoomEvent, RoomPost
from heimdall.modules.approval.api import approve_by_policy
from heimdall.modules.approval.models import Approval
from heimdall.modules.desk.api import format_approval_message, get_draft, save_draft
from heimdall.modules.tenancy.models import Repo
from tests.integration.db.test_approval_service import (
    MaintainerConfigReader,
    _draft_context,
)

pytestmark = pytest.mark.integration


class FakeSwitch:
    """Provide one post and reactor identity to the bridge handler."""

    def __init__(self, message: str, username: str) -> None:
        """Keep the post and user data returned by fake Switch endpoints."""
        self.message = message
        self.username = username
        self.replies: list[tuple[str, str, str]] = []

    async def get_post(self, post_id: str) -> RoomPost:
        """Return the approval post under test."""
        return RoomPost(post_id, "channel-1", "", self.message)

    async def get_username(self, user_id: str) -> str:
        """Return the configured fake Switch username."""
        return self.username

    async def post_to_thread(self, channel_id: str, root_id: str, message: str) -> str:
        """Record a bridge reply and return its fake post id."""
        self.replies.append((channel_id, root_id, message))
        return "reply-1"


async def test_switch_reaction_records_approval_and_rejects_non_maintainer(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Reject an outsider without writes, then approve through a fake Switch room."""
    ctx, repo, _, draft_id = await _draft_context(sessions)
    draft = await get_draft(sessions, ctx, draft_id)
    post_message = format_approval_message(
        draft.body,
        draft.id,
        draft.body_hash,
        int(ctx.installation_id),
        repo.full_name,
    )
    stranger_room = FakeSwitch(post_message, "stranger")
    stranger_handler = ApprovalReactionHandler(
        sessions,
        MaintainerConfigReader(),
        stranger_room,  # type: ignore[arg-type]
    )
    event = RoomEvent("reaction_added", "channel-1", "post-1", "user-1", emoji_name="thumbsup")

    await stranger_handler.handle(event)

    assert stranger_room.replies == [("channel-1", "post-1", NON_MAINTAINER_REPLY)]
    async with unit_of_work(sessions) as session:
        approvals = await session.exec(
            select_for_tenant(Approval, ctx.installation_id).where(Approval.draft_id == draft_id)
        )
        comments = await session.exec(
            select_for_tenant(OutboxMessage, ctx.installation_id).where(
                OutboxMessage.kind == "github.pr_comment"
            )
        )
    assert approvals.first() is None
    assert comments.first() is None

    maintainer_room = FakeSwitch(post_message, "reviewer")
    maintainer_handler = ApprovalReactionHandler(
        sessions,
        MaintainerConfigReader(),
        maintainer_room,  # type: ignore[arg-type]
    )
    await maintainer_handler.handle(event)

    async with unit_of_work(sessions) as session:
        approvals = await session.exec(
            select_for_tenant(Approval, ctx.installation_id).where(Approval.draft_id == draft_id)
        )
        comments = await session.exec(
            select_for_tenant(OutboxMessage, ctx.installation_id).where(
                OutboxMessage.kind == "github.pr_comment"
            )
        )
    assert approvals.one().draft_hash == draft.body_hash
    assert len(comments.all()) == 1


async def test_switch_reaction_ignores_unmarked_posts(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Ignore ordinary thumbs-up reactions that do not target a draft message."""
    room = FakeSwitch("ordinary room message", "reviewer")
    handler = ApprovalReactionHandler(sessions, MaintainerConfigReader(), room)  # type: ignore[arg-type]
    event = RoomEvent("reaction_added", "channel-1", "post-1", "user-1", emoji_name="+1")

    await handler.handle(event)

    assert room.replies == []


async def test_policy_approval_and_maintainer_pause_are_tenant_scoped(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Approve allowed fixes by policy, then stop future approvals on maintainer request."""
    ctx, repo, pr_id, draft_id = await _draft_context(sessions)
    reader = MaintainerConfigReader()
    approval = await approve_by_policy(sessions, reader, ctx, repo, draft_id, "lint")
    assert approval.approver == "policy:auto_suggest"

    paused_draft = await save_draft(sessions, ctx, pr_id, "head-1", "summary", "Next fix")
    message = format_approval_message(
        paused_draft.body,
        paused_draft.id,
        paused_draft.body_hash,
        int(ctx.installation_id),
        repo.full_name,
    )
    room = FakeSwitch(message, "reviewer")
    handler = ApprovalReactionHandler(sessions, reader, room)  # type: ignore[arg-type]
    event = RoomEvent(
        "posted",
        "channel-1",
        "command-1",
        "user-1",
        root_id="post-1",
        message="@heimdall pause auto",
    )

    await handler.handle(event)

    assert room.replies == [("channel-1", "post-1", AUTO_PAUSED_REPLY)]
    async with unit_of_work(sessions) as session:
        repo_result = await session.exec(
            select_for_tenant(Repo, ctx.installation_id).where(Repo.full_name == repo.full_name)
        )
        stored_repo = repo_result.one()
    assert stored_repo.auto_suggest_paused
    with pytest.raises(ValueError, match="disabled by repository policy"):
        await approve_by_policy(sessions, reader, ctx, repo, paused_draft.id, "lint")
