"""A maintainer's free-text reply is read by the model; only a clear go-ahead approves."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from agents._conversation import Question
from agents._mention import SENDER_ROLE_MAINTAINER
from agents._mention_ports import DbMentionPorts, MentionDeps
from agents.heimdall._approve_message import APPROVAL_INTENT_TOOL, approve_from_message
from agents.heimdall._intake_job import run_intake_reply
from agents.heimdall.mention import MENTION_AGENT, guard_act_tools
from heimdall.adapters.ai import ToolCall
from heimdall.adapters.db.outbox.models import OutboxMessage
from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.approval.models import Approval
from heimdall.shared.context import TenantCtx
from heimdall.shared.github import RepoRef
from tests.integration.db.test_intake_reply_job import HEAD, failed_pr
from tests.unit.agents.scripted_ai import ScriptedAi, text_reply, tool_reply

pytestmark = pytest.mark.integration

AGENT_ROOM = "00000000-0000-0000-0000-00000000c001"


class _Reader:
    """Repo config naming the same maintainers the database holds."""

    async def get_file(self, repo: RepoRef, path: str, ref: str | None) -> str | None:
        return "maintainers:\n  - alice\n  - bob\n" if path == "heimdall.yml" else None


def _verdict(approves: bool) -> ToolCall:
    return ToolCall("v", APPROVAL_INTENT_TOOL, {"approves": approves})


async def _setup(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine, *, draft: bool
) -> tuple[TenantCtx, uuid.UUID]:
    ctx, pr_id, installation_id = await failed_pr(sessions, engine, agent_room=AGENT_ROOM)
    if draft:
        await run_intake_reply(
            sessions,
            ScriptedAi([text_reply("- Link an issue.")]),
            installation_id,
            str(pr_id),
            HEAD,
        )
    return ctx, pr_id


async def _approve(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    pr_id: uuid.UUID,
    ai: ScriptedAi,
    sender: str,
    text: str,
    role: str = SENDER_ROLE_MAINTAINER,
) -> str | None:
    deps = MentionDeps(MENTION_AGENT, sessions, _Reader(), lambda: ai, guard_act_tools)  # type: ignore[arg-type]
    ports = DbMentionPorts(deps, ctx, role)
    answer = await approve_from_message(
        sessions,
        _Reader(),
        ports,
        ctx,
        ai,
        Question(str(pr_id), sender, text),  # type: ignore[arg-type]
    )
    return None if answer is None else answer.text


async def _approvals(
    sessions: async_sessionmaker[AsyncSession], ctx: TenantCtx
) -> tuple[list[Approval], list[OutboxMessage]]:
    async with unit_of_work(sessions) as session:
        rows = (await session.exec(select_for_tenant(Approval, ctx.installation_id))).all()
        comments = (
            await session.exec(
                select_for_tenant(OutboxMessage, ctx.installation_id).where(
                    OutboxMessage.kind == "github.pr_comment"
                )
            )
        ).all()
    return list(rows), list(comments)


@pytest.mark.parametrize("phrase", ["do it", "go ahead", "yes please", "lgtm, ship it"])
async def test_a_plain_go_ahead_from_a_maintainer_approves_the_pending_draft(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine, phrase: str
) -> None:
    ctx, pr_id = await _setup(sessions, engine, draft=True)

    reply = await _approve(
        sessions, ctx, pr_id, ScriptedAi([tool_reply(_verdict(True))]), "alice", phrase
    )

    approvals, comments = await _approvals(sessions, ctx)
    assert reply is not None and "approved" in reply.lower()
    assert [(a.approver, a.via) for a in approvals] == [("alice", "message")]
    assert len(comments) == 1


async def test_a_message_that_does_not_approve_is_left_for_the_normal_conversation(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    ctx, pr_id = await _setup(sessions, engine, draft=True)

    reply = await _approve(
        sessions, ctx, pr_id, ScriptedAi([tool_reply(_verdict(False))]), "alice", "why did it fail?"
    )

    approvals, comments = await _approvals(sessions, ctx)
    assert reply is None and approvals == [] and comments == []


async def test_a_non_maintainer_is_never_asked_to_approve_and_approves_nothing(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    ctx, pr_id = await _setup(sessions, engine, draft=True)
    ai = ScriptedAi([])  # any model call would raise

    reply = await _approve(sessions, ctx, pr_id, ai, "mallory", "do it", role="contributor")

    approvals, _ = await _approvals(sessions, ctx)
    assert reply is None and approvals == [] and ai.requests == []


async def test_without_a_pending_draft_the_model_is_not_asked(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    ctx, pr_id = await _setup(sessions, engine, draft=False)
    ai = ScriptedAi([])

    reply = await _approve(sessions, ctx, pr_id, ai, "alice", "go ahead")

    assert reply is None and ai.requests == []


async def test_the_model_sees_only_the_message_as_marked_data(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    ctx, pr_id = await _setup(sessions, engine, draft=True)
    ai = ScriptedAi([tool_reply(_verdict(False))])

    await _approve(sessions, ctx, pr_id, ai, "alice", "go ahead")

    sent = " ".join(m.content for m in ai.requests[0][0])
    assert "<pr_data" in sent and "go ahead" in sent
    assert "Link an issue" not in sent  # the draft body is not given to the model
