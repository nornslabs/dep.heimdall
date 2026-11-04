"""What an agent job needs to know about its PR, read once and checked for staleness."""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.tenancy.api import get_repo_full_name_in_transaction, repo_agent_room_id
from heimdall.modules.triage.api import PrSnapshot, get_pr_snapshot
from heimdall.shared.context import TenantCtx


@dataclass(frozen=True)
class PrJobContext:
    """The PR, its repository name and its room, as they were when the job started.

    Attributes:
        snapshot: Triage's view of the PR.
        repo_full_name: ``owner/name`` of the PR's repository.
        agent_room_id: Switch room UUID the agents post to, or None when the repo has none.
    """

    snapshot: PrSnapshot
    repo_full_name: str
    agent_room_id: str | None


async def load_pr_job_context(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    pr_id: uuid.UUID,
    head_sha: str,
) -> PrJobContext | None:
    """Read the PR for an agent job, or None when the job no longer matches the PR's head.

    A push after the job was queued re-runs intake and queues fresh jobs, so a job for an
    older head has nothing left to do.

    Args:
        sessions: Tenant database session factory.
        ctx: Tenant and request context.
        pr_id: Triage PR row identifier.
        head_sha: Head the job was queued for.

    Returns:
        The job context, or None when the PR is gone or its head moved.
    """
    async with unit_of_work(sessions) as session:
        snapshot = await get_pr_snapshot(session, ctx, pr_id)
        if snapshot is None or snapshot.head_sha != head_sha:
            return None
        repo_full_name = await get_repo_full_name_in_transaction(session, ctx, snapshot.repo_id)
        agent_room_id = await repo_agent_room_id(session, ctx, snapshot.repo_id)
    return PrJobContext(snapshot, repo_full_name, agent_room_id)


async def load_agent_room_id(
    sessions: async_sessionmaker[AsyncSession], ctx: TenantCtx, pr_id: uuid.UUID
) -> str | None:
    """Read the Switch room UUID a PR's agents post to, for a job that needs no staleness check.

    Args:
        sessions: Tenant database session factory.
        ctx: Tenant and request context.
        pr_id: Triage PR row identifier.

    Returns:
        The room id, or None when the PR is gone or its repo has no agent room.
    """
    async with unit_of_work(sessions) as session:
        snapshot = await get_pr_snapshot(session, ctx, pr_id)
        if snapshot is None:
            return None
        return await repo_agent_room_id(session, ctx, snapshot.repo_id)
