"""Run the intake use case and persist one result set per PR head."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.intake._checklist import render_checklist
from heimdall.modules.intake._github_facts import collect_intake_facts, linked_issue_number
from heimdall.modules.intake._rules import evaluate_rules
from heimdall.modules.intake._stale import mark_results_stale
from heimdall.modules.intake.domain import IntakeFacts, RuleResult
from heimdall.modules.intake.ports import IntakeReader
from heimdall.modules.tenancy.api import load_config, repo_switch_room_id
from heimdall.modules.triage.api import (
    EventKind,
    Facts,
    Rejected,
    get_pr_snapshot,
    on_step_done_in_transaction,
)
from heimdall.shared.context import TenantCtx
from heimdall.shared.errors import StaleHeadSha
from heimdall.shared.github import RepoRef
from heimdall.shared.ids import RepoId

UPSERT_RESULT = text(
    """
    INSERT INTO intake_results
        (id, installation_id, pr_id, head_sha, rule, passed, detail, stale, checked_at)
    VALUES
        (:id, :installation_id, :pr_id, :head_sha, :rule, :passed, :detail, :stale, :checked_at)
    ON CONFLICT (pr_id, head_sha, rule) DO UPDATE SET
        passed = EXCLUDED.passed, detail = EXCLUDED.detail,
        stale = EXCLUDED.stale, checked_at = EXCLUDED.checked_at
    """
)


async def run_intake(
    sessions: async_sessionmaker[AsyncSession],
    reader: IntakeReader,
    ctx: TenantCtx,
    repo: RepoRef,
    repo_id: RepoId,
    pr_id: uuid.UUID,
    number: int,
    head_sha: str,
    event_id: str,
) -> tuple[RuleResult, ...]:
    """Evaluate house rules and atomically save results, checklist and PR transition.

    Args:
        sessions: Tenant database session factory.
        reader: GitHub reader used for configuration and PR facts.
        ctx: Tenant and request context.
        repo: Repository the PR belongs to.
        repo_id: Database and GitHub repository identifier for cross-checking the PR.
        pr_id: PR database row id.
        number: GitHub PR number.
        head_sha: Commit the queued intake job evaluates.
        event_id: Stable job idempotency key.

    Returns:
        Six rule outcomes in checklist order.

    Raises:
        StaleHeadSha: GitHub's PR head no longer matches this job.
    """
    config = await load_config(sessions, reader, ctx, repo, head_sha)
    pull_request = await reader.get_pull_request(repo, number)
    if pull_request.head_sha != head_sha:
        raise StaleHeadSha("pull request head changed before intake started")
    facts = await collect_intake_facts(reader, repo, pull_request, config.intake)
    results = evaluate_rules(facts, config.intake)
    await _save_results_and_transition(
        sessions,
        ctx,
        repo_id,
        pr_id,
        event_id,
        head_sha,
        facts,
        results,
        config.repro.test_cmd,
    )
    return results


async def _save_results_and_transition(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    repo_id: RepoId,
    pr_id: uuid.UUID,
    event_id: str,
    head_sha: str,
    facts: IntakeFacts,
    results: tuple[RuleResult, ...],
    repro_test_cmd: str,
) -> None:
    """Commit result rows and the matching state transition together."""
    async with unit_of_work(sessions) as session:
        pr = await get_pr_snapshot(session, ctx, pr_id)
        if pr is None:
            raise ValueError(f"pull request {pr_id} is not registered for this installation")
        if pr.repo_id != repo_id:
            raise ValueError("pull request does not belong to the requested repository")
        is_current = pr.head_sha == head_sha
        await _upsert_results(session, ctx, pr_id, head_sha, results, stale=not is_current)
        if not is_current:
            return
        room_id = await repo_switch_room_id(session, ctx, RepoId(pr.repo_id))
        message = render_checklist(pr.number, results)
        transition = await on_step_done_in_transaction(
            session,
            ctx,
            pr.id,
            event_id,
            EventKind.INTAKE_DONE,
            head_sha,
            Facts(intake_passed=all(result.passed for result in results)),
            room_id=room_id,
            room_message=message,
            linked_issue=linked_issue_number(facts.body),
            repro_test_cmd=repro_test_cmd,
        )
        if isinstance(transition, Rejected):
            await session.refresh(pr)
            if pr.head_sha != head_sha:
                await mark_results_stale(session, ctx, str(pr_id), pr.head_sha)


async def _upsert_results(
    session: AsyncSession,
    ctx: TenantCtx,
    pr_id: uuid.UUID,
    head_sha: str,
    results: tuple[RuleResult, ...],
    *,
    stale: bool,
) -> None:
    """Upsert each rule result so retried intake jobs remain idempotent."""
    connection = await session.connection()
    checked_at = datetime.now(UTC)
    for result in results:
        await connection.execute(
            UPSERT_RESULT,
            {
                "id": uuid.uuid4(),
                "installation_id": ctx.installation_id,
                "pr_id": pr_id,
                "head_sha": head_sha,
                "rule": result.rule.value,
                "passed": result.passed,
                "detail": result.detail,
                "stale": stale,
                "checked_at": checked_at,
            },
        )
