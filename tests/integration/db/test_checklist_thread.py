"""Integration coverage for intake's checklist opening the PR's thread."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.tenancy.api import link_repo_room, on_installation_event
from heimdall.modules.triage.api import (
    EventKind,
    Facts,
    PrEvent,
    handle_event,
    on_step_done,
    save_switch_thread_id,
)
from heimdall.modules.triage.models import Pr
from heimdall.shared.context import TenantCtx
from heimdall.shared.ids import InstallationId, RepoId
from tests.integration.db.installation_helpers import created_installation

pytestmark = pytest.mark.integration

MATTERMOST_CHANNEL = "mm-channel"
AGENT_ROOM = "00000000-0000-0000-0000-00000000a001"


async def _open_pr(
    sessions: async_sessionmaker[AsyncSession], *, agent_room: str | None
) -> tuple[TenantCtx, uuid.UUID]:
    """Create a repo linked to a room, open a PR in it, and return its tenant and row id."""
    installation_id = int(uuid.uuid4().int % 1_000_000_000) + 1_000_000
    repo_id = RepoId(installation_id + 1)
    ctx = TenantCtx(installation_id=InstallationId(installation_id), request_id="checklist-test")
    await on_installation_event(
        sessions,
        created_installation(installation_id, "checklist-test", repo_id, "checklist-test/demo"),
    )
    await link_repo_room(sessions, ctx, repo_id, MATTERMOST_CHANNEL, agent_room_id=agent_room)
    await handle_event(
        sessions,
        ctx,
        PrEvent(
            event_id="open-checklist",
            repo_id=repo_id,
            repo_full_name="checklist-test/demo",
            number=1,
            author="octocat",
            head_sha="head-1",
            base_sha="base-1",
            base_ref="main",
            kind=EventKind.PR_OPENED,
            facts=Facts(),
        ),
    )
    async with unit_of_work(sessions) as session:
        pr = (
            await session.exec(
                select_for_tenant(Pr, ctx.installation_id).where(Pr.repo_id == repo_id)
            )
        ).one()
        return ctx, pr.id


async def _checklist_payload(
    sessions: async_sessionmaker[AsyncSession], event_id: str, effect: str
) -> dict[str, object]:
    async with unit_of_work(sessions) as session:
        connection = await session.connection()
        row = await connection.execute(
            text("SELECT payload FROM outbox WHERE idempotency_key = :key"),
            {"key": f"triage:{event_id}:{effect}"},
        )
        payload: dict[str, object] = row.scalar_one()
        return payload


async def test_the_checklist_is_posted_by_heimdall_as_the_thread_root(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ctx, pr_id = await _open_pr(sessions, agent_room=AGENT_ROOM)

    await on_step_done(
        sessions,
        ctx,
        pr_id,
        "intake-ok",
        EventKind.INTAKE_DONE,
        "head-1",
        Facts(intake_passed=True),
        room_id=MATTERMOST_CHANNEL,
        agent_room_id=AGENT_ROOM,
        room_message="Intake checklist for pull request #1",
    )

    assert await _checklist_payload(sessions, "intake-ok", "outbox_thread_and_checklist") == {
        "agent": "heimdall",
        "channel_id": AGENT_ROOM,
        "message": "Intake checklist for pull request #1",
        "pr_id": str(pr_id),
        "thread_role": "root",
    }


async def test_a_failed_intake_also_opens_the_thread(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ctx, pr_id = await _open_pr(sessions, agent_room=AGENT_ROOM)

    await on_step_done(
        sessions,
        ctx,
        pr_id,
        "intake-failed",
        EventKind.INTAKE_DONE,
        "head-1",
        Facts(intake_passed=False),
        room_id=MATTERMOST_CHANNEL,
        agent_room_id=AGENT_ROOM,
        room_message="Intake failed: a label is missing.",
    )

    payload = await _checklist_payload(sessions, "intake-failed", "outbox_failed_rules")
    assert payload["agent"] == "heimdall"
    assert payload["channel_id"] == AGENT_ROOM
    assert payload["pr_id"] == str(pr_id)
    assert payload["thread_role"] == "root"


async def test_a_repo_with_no_agent_room_keeps_the_bot_checklist(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ctx, pr_id = await _open_pr(sessions, agent_room=None)

    await on_step_done(
        sessions,
        ctx,
        pr_id,
        "intake-bot",
        EventKind.INTAKE_DONE,
        "head-1",
        Facts(intake_passed=True),
        room_id=MATTERMOST_CHANNEL,
        agent_room_id=None,
        room_message="Intake checklist for pull request #1",
    )

    assert await _checklist_payload(sessions, "intake-bot", "outbox_thread_and_checklist") == {
        "channel_id": MATTERMOST_CHANNEL,
        "message": "Intake checklist for pull request #1",
    }


HEADING = "New PR #1 Fix login"


async def _outbox_payload_or_none(
    sessions: async_sessionmaker[AsyncSession], event_id: str, effect: str
) -> dict[str, object] | None:
    async with unit_of_work(sessions) as session:
        connection = await session.connection()
        row = await connection.execute(
            text("SELECT payload FROM outbox WHERE idempotency_key = :key"),
            {"key": f"triage:{event_id}:{effect}"},
        )
        payload: dict[str, object] | None = row.scalar_one_or_none()
        return payload


async def _finish_intake(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    pr_id: uuid.UUID,
    event_id: str,
) -> None:
    await on_step_done(
        sessions,
        ctx,
        pr_id,
        event_id,
        EventKind.INTAKE_DONE,
        "head-1",
        Facts(intake_passed=True),
        room_id=MATTERMOST_CHANNEL,
        agent_room_id=AGENT_ROOM,
        room_message="Intake checklist for pull request #1",
        thread_heading=HEADING,
    )


async def test_the_heading_is_the_thread_root_and_the_checklist_replies_under_it(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ctx, pr_id = await _open_pr(sessions, agent_room=AGENT_ROOM)

    await _finish_intake(sessions, ctx, pr_id, "intake-heading")

    assert await _outbox_payload_or_none(sessions, "intake-heading", "thread_heading") == {
        "agent": "heimdall",
        "channel_id": AGENT_ROOM,
        "message": HEADING,
        "pr_id": str(pr_id),
        "thread_role": "root",
    }
    checklist = await _checklist_payload(sessions, "intake-heading", "outbox_thread_and_checklist")
    assert checklist["pr_id"] == str(pr_id)
    assert "thread_role" not in checklist


async def test_a_repeat_push_adds_a_checklist_reply_but_no_second_heading(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ctx, pr_id = await _open_pr(sessions, agent_room=AGENT_ROOM)
    await save_switch_thread_id(sessions, ctx, pr_id, "sw_root")

    await _finish_intake(sessions, ctx, pr_id, "intake-repeat")

    assert await _outbox_payload_or_none(sessions, "intake-repeat", "thread_heading") is None
    checklist = await _checklist_payload(sessions, "intake-repeat", "outbox_thread_and_checklist")
    assert "thread_role" not in checklist


async def test_a_repo_with_no_agent_room_gets_no_heading(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ctx, pr_id = await _open_pr(sessions, agent_room=None)

    await on_step_done(
        sessions,
        ctx,
        pr_id,
        "intake-flat",
        EventKind.INTAKE_DONE,
        "head-1",
        Facts(intake_passed=True),
        room_id=MATTERMOST_CHANNEL,
        agent_room_id=None,
        room_message="Intake checklist for pull request #1",
        thread_heading=HEADING,
    )

    assert await _outbox_payload_or_none(sessions, "intake-flat", "thread_heading") is None
