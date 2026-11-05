"""A failed intake yields one saved draft, posted to the PR thread with every maintainer tagged."""

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from agents.heimdall._intake_job import INTAKE_REPLY_KIND, run_intake_reply
from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.desk.models import Draft
from heimdall.modules.tenancy.api import get_tenant, link_repo_room, on_installation_event
from heimdall.modules.triage.api import EventKind, Facts, PrEvent, handle_event
from heimdall.shared.context import TenantCtx
from heimdall.shared.ids import InstallationId, RepoId
from tests.integration.db.installation_helpers import created_installation
from tests.unit.agents.scripted_ai import ScriptedAi, text_reply

pytestmark = pytest.mark.integration

AGENT_ROOM = "00000000-0000-0000-0000-00000000b001"
HEAD = "head-1"


async def failed_pr(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine, *, agent_room: str | None
) -> tuple[TenantCtx, uuid.UUID, int]:
    installation_id = int(uuid.uuid4().int % 1_000_000_000) + 1_000_000
    repo_id = RepoId(installation_id + 1)
    full_name = "reply-test/demo"
    await on_installation_event(
        sessions, created_installation(installation_id, "reply-test", repo_id, full_name)
    )
    ctx = await get_tenant(sessions, InstallationId(installation_id))
    await link_repo_room(sessions, ctx, repo_id, "mm-channel", agent_room_id=agent_room)
    await handle_event(
        sessions,
        ctx,
        PrEvent(
            event_id="open-reply",
            repo_id=repo_id,
            repo_full_name=full_name,
            number=24,
            author="octocat",
            head_sha=HEAD,
            base_sha="base-1",
            base_ref="main",
            kind=EventKind.PR_OPENED,
            facts=Facts(),
        ),
    )
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE repos SET config = CAST(:config AS jsonb) WHERE repo_id = :id"),
            {"config": json.dumps({"maintainers": ["alice", "bob"]}), "id": repo_id},
        )
        pr_id = (
            await connection.execute(
                text("SELECT id FROM prs WHERE repo_id = :id"), {"id": repo_id}
            )
        ).scalar_one()
        for rule, passed, detail in (
            ("linked_issue", False, "Add `Closes #n` to the pull request body."),
            ("required_labels", False, "Add required labels: hackathon."),
            ("ci_green", True, "CI is green."),
        ):
            await connection.execute(
                text(
                    "INSERT INTO intake_results (id, installation_id, pr_id, head_sha, rule, "
                    "passed, detail, stale, checked_at) VALUES (:id, :inst, :pr, :head, :rule, "
                    ":passed, :detail, false, now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "inst": installation_id,
                    "pr": pr_id,
                    "head": HEAD,
                    "rule": rule,
                    "passed": passed,
                    "detail": detail,
                },
            )
    return ctx, pr_id, installation_id


async def _outbox_payloads(engine: AsyncEngine, installation_id: int) -> list[dict[str, object]]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT payload FROM outbox WHERE installation_id = :id "
                "AND idempotency_key LIKE '%intake_reply%'"
            ),
            {"id": installation_id},
        )
        return [row[0] for row in rows]


async def test_failed_intake_saves_one_draft_and_tags_every_maintainer(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    ctx, pr_id, installation_id = await failed_pr(sessions, engine, agent_room=AGENT_ROOM)
    ai = ScriptedAi([text_reply("- Link an issue with `Closes #n`.\n- Add the hackathon label.")])

    await run_intake_reply(sessions, ai, installation_id, str(pr_id), HEAD)

    async with unit_of_work(sessions) as session:
        drafts = (
            await session.exec(
                select_for_tenant(Draft, ctx.installation_id).where(Draft.pr_id == pr_id)
            )
        ).all()
    assert [draft.kind for draft in drafts] == [INTAKE_REPLY_KIND]
    assert drafts[0].body.startswith("Hey @octocat,")
    (payload,) = await _outbox_payloads(engine, installation_id)
    message = str(payload["message"])
    assert message.startswith("@alice @bob")
    assert drafts[0].body in message
    assert f"draft_hash={drafts[0].body_hash}" in message
    assert payload["pr_id"] == str(pr_id)
    assert payload["channel_id"] == AGENT_ROOM


async def test_passing_intake_drafts_nothing(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    _ctx, pr_id, installation_id = await failed_pr(sessions, engine, agent_room=AGENT_ROOM)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE intake_results SET passed = true WHERE pr_id = :id"), {"id": pr_id}
        )

    await run_intake_reply(sessions, ScriptedAi([]), installation_id, str(pr_id), HEAD)

    assert await _outbox_payloads(engine, installation_id) == []


async def test_stale_head_drafts_nothing(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    _ctx, pr_id, installation_id = await failed_pr(sessions, engine, agent_room=AGENT_ROOM)

    await run_intake_reply(sessions, ScriptedAi([]), installation_id, str(pr_id), "older-head")

    assert await _outbox_payloads(engine, installation_id) == []


async def test_repo_without_agent_room_saves_the_draft_but_posts_nothing(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    _ctx, pr_id, installation_id = await failed_pr(sessions, engine, agent_room=None)

    await run_intake_reply(
        sessions, ScriptedAi([text_reply("- Fix it.")]), installation_id, str(pr_id), HEAD
    )

    assert await _outbox_payloads(engine, installation_id) == []
