"""The ``heimdall.draft_intake_reply`` job: draft, save and ask maintainers to approve a reply.

A failed intake queues this job. Nothing here talks to GitHub: the draft is saved with its hash and
posted to the PR's thread with every configured maintainer tagged, and only an approval that
matches the hash lets the outbox post it.
"""

import re
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from agents._job_context import load_pr_job_context
from agents._runtime import ChatModel
from agents.heimdall._intake_reply import draft_intake_reply
from heimdall.adapters.db.outbox.writer import write_outbox
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.desk.api import format_approval_message, mention_maintainers, save_draft
from heimdall.modules.intake.api import list_failed_rules
from heimdall.modules.tenancy.api import repo_maintainers
from heimdall.shared.agents import AgentName
from heimdall.shared.context import TenantCtx, new_request_id
from heimdall.shared.errors import StaleHeadSha
from heimdall.shared.ids import InstallationId
from heimdall.shared.logging import get_logger

INTAKE_REPLY_KIND = "intake_reply"
APPROVAL_HINT = (
    "Draft reply to the pull request author. React with 👍 or reply `/heimdall approve` "
    "in this thread to post it exactly as written."
)
_GITHUB_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")

log = get_logger(__name__)


async def run_intake_reply(
    sessions: async_sessionmaker[AsyncSession],
    ai: ChatModel,
    installation_id: int,
    pr_id: str,
    head_sha: str,
) -> None:
    """Draft the reply for a pull request whose intake failed and ask the maintainers to approve.

    Args:
        sessions: Tenant database session factory.
        ai: Heimdall's chat model.
        installation_id: Tenant that owns the pull request.
        pr_id: Triage PR row id.
        head_sha: Head commit whose failed intake queued this job; a newer head makes it a no-op.
    """
    ctx = TenantCtx(InstallationId(installation_id), new_request_id())
    pr_uuid = uuid.UUID(pr_id)
    job = await load_pr_job_context(sessions, ctx, pr_uuid, head_sha)
    if job is None:
        log.info("intake_reply_stale", installation_id=installation_id, pr_id=pr_id)
        return
    failed = await list_failed_rules(sessions, ctx, pr_uuid, head_sha)
    if not failed:
        return
    author = job.snapshot.author
    if _GITHUB_LOGIN.fullmatch(author) is None:
        log.warning("intake_reply_bad_author", installation_id=installation_id, pr_id=pr_id)
        return
    body = await draft_intake_reply(ai, author, job.snapshot.number, failed)
    try:
        draft = await save_draft(sessions, ctx, pr_uuid, head_sha, INTAKE_REPLY_KIND, body)
    except StaleHeadSha:
        log.info("intake_reply_stale", installation_id=installation_id, pr_id=pr_id)
        return
    if not job.agent_room_id:
        log.warning("intake_reply_not_posted_no_room", installation_id=installation_id, pr_id=pr_id)
        return
    async with unit_of_work(sessions) as session:
        maintainers = await repo_maintainers(session, ctx, job.snapshot.repo_id)
        approval_post = format_approval_message(
            draft.body, draft.id, draft.body_hash, installation_id, job.repo_full_name
        )
        await write_outbox(
            session,
            installation_id=ctx.installation_id,
            channel="switch",
            kind="room.message",
            payload={
                "agent": AgentName.HEIMDALL.value,
                "channel_id": job.agent_room_id,
                "message": mention_maintainers(f"{APPROVAL_HINT}\n\n{approval_post}", maintainers),
                "pr_id": pr_id,
            },
            idempotency_key=f"heimdall:{installation_id}:{pr_id}:{head_sha}:intake_reply:{draft.id}",
        )
