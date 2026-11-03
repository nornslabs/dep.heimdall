"""Transactional triage state transitions."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.outbox.writer import write_outbox
from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.adapters.queue.enqueue import HUGINN_QUEUE, enqueue
from heimdall.modules.tenancy.api import repo_is_enabled
from heimdall.modules.triage._webhook_event import pull_request_event
from heimdall.modules.triage.domain import (
    Effect,
    EventKind,
    Facts,
    PrEvent,
    PrSnapshot,
    PrState,
    Rejected,
    RejectReason,
    Transition,
    next_state,
)
from heimdall.modules.triage.models import Event, Pr
from heimdall.shared.context import TenantCtx
from heimdall.shared.ids import RepoId

UPDATE_PR_STATE = text(
    """
    UPDATE prs
    SET state = :state, state_version = state_version + 1,
        head_sha = :head_sha, base_sha = :base_sha, base_ref = :base_ref, author = :author,
        linked_issue = :linked_issue, updated_at = now()
    WHERE id = :pr_id AND installation_id = :installation_id
        AND state_version = :state_version
    """
)


async def handle_event(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    event: PrEvent,
) -> Transition | Rejected:
    """Apply one validated event using an optimistic PR state version check.

    Args:
        sessions: Session factory for the transaction.
        ctx: Tenant and request context.
        event: Validated event facts and stable idempotency key.

    Returns:
        The transition, or the reason the event was rejected.
    """
    async with unit_of_work(sessions) as session:
        repo_enabled = await repo_is_enabled(session, ctx, event.repo_id)
        facts = replace(event.facts, repo_enabled=repo_enabled)
        result = await session.exec(
            select(Pr).where(
                Pr.installation_id == ctx.installation_id,
                Pr.repo_id == event.repo_id,
                Pr.number == event.number,
            )
        )
        pr = result.first()
        if pr is None:
            return await _create_pr(session, ctx, event, facts)
        transition = next_state(
            PrState(pr.state),
            event.kind,
            replace(facts, result_is_for_current_head=event.head_sha == pr.head_sha),
        )
        if isinstance(transition, Rejected):
            return transition
        updated = await _update_pr_state(
            session,
            ctx,
            pr,
            transition,
            head_sha=event.head_sha,
            base_sha=event.base_sha,
            base_ref=event.base_ref,
            author=event.author,
            linked_issue=event.linked_issue,
        )
        if not updated:
            return Rejected(RejectReason.NOT_ALLOWED_FROM_STATE)
        await _apply_effects(session, ctx, event, transition.effects, pr.id)
        return transition


async def handle_webhook_delivery(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    delivery_id: str,
) -> Transition | Rejected | None:
    """Load one persisted delivery, apply its PR event and mark it handled.

    Args:
        sessions: Session factory for webhook handling.
        ctx: Tenant and request context.
        delivery_id: GitHub delivery id stored by the API endpoint.

    Returns:
        The state transition, or the reason the webhook was rejected.
    """
    async with unit_of_work(sessions) as session:
        result = await session.exec(
            select_for_tenant(Event, ctx.installation_id).where(Event.delivery_id == delivery_id)
        )
        stored = result.first()
        if stored is None:
            return Rejected(RejectReason.NOT_ALLOWED_FROM_STATE)
        event_name = stored.kind
        action = stored.action
        payload = dict(stored.payload)
    if event_name in {"installation", "installation_repositories"}:
        from heimdall.modules.tenancy.api import (
            on_installation_event,
            parse_installation_event,
        )

        installation_event = parse_installation_event(event_name, payload)
        if installation_event is not None:
            await on_installation_event(sessions, installation_event)
        await _mark_delivery_handled(sessions, ctx, delivery_id)
        return None
    event = pull_request_event(delivery_id, event_name, action, payload)
    if event is None:
        await _mark_delivery_handled(sessions, ctx, delivery_id)
        return Rejected(RejectReason.NOT_ALLOWED_FROM_STATE)
    transition = await handle_event(sessions, ctx, event)
    await _mark_delivery_handled(sessions, ctx, delivery_id)
    return transition


async def _mark_delivery_handled(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    delivery_id: str,
) -> None:
    """Record successful processing without changing the original delivery."""
    async with unit_of_work(sessions) as session:
        result = await session.exec(
            select_for_tenant(Event, ctx.installation_id).where(Event.delivery_id == delivery_id)
        )
        stored = result.first()
        if stored is not None:
            stored.handled_at = datetime.now(UTC)


async def on_step_done(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    pr_id: UUID,
    event_id: str,
    kind: EventKind,
    head_sha: str,
    facts: Facts,
    *,
    room_id: str | None = None,
    room_message: str | None = None,
    repro_test_cmd: str | None = None,
) -> Transition | Rejected:
    """Apply a completed intake, reproduction, or review step to its PR.

    Args:
        sessions: Session factory for the transaction.
        ctx: Tenant and request context.
        pr_id: PR row receiving the result.
        event_id: Stable idempotency key for the completed job.
        kind: Completed step event.
        head_sha: Commit the step evaluated.
        facts: Current rule and agent build results.
        room_id: Optional Switch room for result messages.
        room_message: Rendered checklist or failure explanation.
        repro_test_cmd: Validated repository test command queued for Huginn.

    Returns:
        The transition, or the reason the result was rejected.
    """
    async with unit_of_work(sessions) as session:
        return await on_step_done_in_transaction(
            session,
            ctx,
            pr_id,
            event_id,
            kind,
            head_sha,
            facts,
            room_id=room_id,
            room_message=room_message,
            repro_test_cmd=repro_test_cmd,
        )


async def on_step_done_in_transaction(
    session: AsyncSession,
    ctx: TenantCtx,
    pr_id: UUID,
    event_id: str,
    kind: EventKind,
    head_sha: str,
    facts: Facts,
    *,
    room_id: str | None = None,
    room_message: str | None = None,
    linked_issue: int | None = None,
    repro_test_cmd: str | None = None,
) -> Transition | Rejected:
    """Apply step completion within an existing feature transaction.

    Args:
        session: Session inside the use case transaction.
        ctx: Tenant and request context.
        pr_id: Tenant-scoped PR row receiving the result.
        event_id: Stable idempotency key for the result.
        kind: Completed step event.
        head_sha: Commit evaluated by the step.
        facts: Rule and build results used by guards.
        room_id: Optional Switch room for resulting outbox messages.
        room_message: Rendered checklist or failure explanation.
        linked_issue: Issue number found during intake, when present.
        repro_test_cmd: Validated repository test command queued for Huginn.

    Returns:
        The transition, or the reason the result was rejected.
    """
    result = await session.exec(select_for_tenant(Pr, ctx.installation_id).where(Pr.id == pr_id))
    pr = result.first()
    if pr is None:
        return Rejected(RejectReason.NOT_ALLOWED_FROM_STATE)
    event = PrEvent(
        event_id=event_id,
        repo_id=RepoId(pr.repo_id),
        number=pr.number,
        author=pr.author,
        head_sha=head_sha,
        base_sha=pr.base_sha,
        base_ref=pr.base_ref,
        kind=kind,
        facts=replace(facts, repo_enabled=True),
        linked_issue=linked_issue if linked_issue is not None else pr.linked_issue,
        room_id=room_id,
        room_message=room_message,
        repro_test_cmd=repro_test_cmd,
    )
    return await _apply_existing_event(session, ctx, pr, event)


async def get_pr_snapshot(
    session: AsyncSession,
    ctx: TenantCtx,
    pr_id: UUID,
) -> PrSnapshot | None:
    """Read a tenant PR through the triage public interface.

    Args:
        session: Session inside the caller's transaction.
        ctx: Tenant context that scopes the lookup.
        pr_id: Triage PR row id.

    Returns:
        Read-only PR data, or None when this tenant does not own the row.
    """
    result = await session.exec(select_for_tenant(Pr, ctx.installation_id).where(Pr.id == pr_id))
    pr = result.first()
    if pr is None:
        return None
    return PrSnapshot(
        id=pr.id,
        repo_id=RepoId(pr.repo_id),
        number=pr.number,
        author=pr.author,
        head_sha=pr.head_sha,
        base_sha=pr.base_sha,
        base_ref=pr.base_ref,
        linked_issue=pr.linked_issue,
        state=PrState(pr.state),
        state_version=pr.state_version,
    )


async def _create_pr(
    session: AsyncSession,
    ctx: TenantCtx,
    event: PrEvent,
    facts: Facts,
) -> Transition | Rejected:
    """Create a new PR row for its first accepted event."""
    if event.kind is not EventKind.PR_OPENED:
        return Rejected(RejectReason.NOT_ALLOWED_FROM_STATE)
    transition = next_state(None, event.kind, facts)
    if isinstance(transition, Rejected):
        return transition
    pr = Pr(
        installation_id=ctx.installation_id,
        repo_id=event.repo_id,
        number=event.number,
        author=event.author,
        head_sha=event.head_sha,
        base_sha=event.base_sha,
        base_ref=event.base_ref,
        linked_issue=event.linked_issue,
        state=transition.to.value,
        state_version=1,
    )
    session.add(pr)
    await session.flush()
    await _apply_effects(session, ctx, event, transition.effects, pr.id)
    return transition


async def _apply_existing_event(
    session: AsyncSession,
    ctx: TenantCtx,
    pr: Pr,
    event: PrEvent,
) -> Transition | Rejected:
    """Advance a loaded PR and apply its effects in the active transaction."""
    transition = next_state(
        PrState(pr.state),
        event.kind,
        replace(event.facts, result_is_for_current_head=event.head_sha == pr.head_sha),
    )
    if isinstance(transition, Rejected):
        return transition
    updated = await _update_pr_state(
        session,
        ctx,
        pr,
        transition,
        head_sha=event.head_sha,
        base_sha=event.base_sha,
        base_ref=event.base_ref,
        author=event.author,
        linked_issue=event.linked_issue,
    )
    if not updated:
        return Rejected(RejectReason.NOT_ALLOWED_FROM_STATE)
    await _apply_effects(session, ctx, event, transition.effects, pr.id)
    return transition


async def _update_pr_state(
    session: AsyncSession,
    ctx: TenantCtx,
    pr: Pr,
    transition: Transition,
    *,
    head_sha: str,
    base_sha: str,
    base_ref: str | None,
    author: str,
    linked_issue: int | None,
) -> bool:
    """Write the state only if no worker has advanced this PR version."""
    connection = await session.connection()
    result = await connection.execute(
        UPDATE_PR_STATE,
        {
            "state": transition.to.value,
            "head_sha": head_sha,
            "base_sha": base_sha,
            "base_ref": base_ref,
            "author": author,
            "linked_issue": linked_issue,
            "pr_id": pr.id,
            "installation_id": ctx.installation_id,
            "state_version": pr.state_version,
        },
    )
    return result.rowcount == 1


async def _apply_effects(
    session: AsyncSession,
    ctx: TenantCtx,
    event: PrEvent,
    effects: tuple[Effect, ...],
    pr_id: UUID,
) -> None:
    """Apply database and queue effects in the state transaction."""
    if Effect.ENQUEUE_INTAKE in effects:
        await enqueue(
            session,
            "intake.run_intake",
            {
                "installation_id": ctx.installation_id,
                "repo_id": event.repo_id,
                "repo_full_name": event.repo_full_name,
                "pr_id": str(pr_id),
                "pr_number": event.number,
                "head_sha": event.head_sha,
                "event_id": event.event_id,
            },
            lock=f"intake:{ctx.installation_id}:{pr_id}:{event.head_sha}",
        )
    if Effect.ROOM_READY_FOR_REPRO in effects and event.repro_test_cmd:
        await enqueue(
            session,
            "huginn.run_repro",
            {
                "installation_id": ctx.installation_id,
                "pr_id": str(pr_id),
                "head_sha": event.head_sha,
                "test_cmd": event.repro_test_cmd,
                "room_id": event.room_id,
            },
            queue=HUGINN_QUEUE,
            lock=f"huginn:{ctx.installation_id}:{pr_id}:{event.head_sha}",
        )
    if Effect.MARK_OLDER_RUNS_STALE in effects:
        from heimdall.modules.intake.api import mark_results_stale
        from heimdall.modules.repro.api import mark_runs_stale

        await mark_results_stale(session, ctx, str(pr_id), event.head_sha)
        await mark_runs_stale(session, ctx, str(pr_id), event.head_sha)
    if event.room_id and event.room_message:
        await _write_room_effects(session, ctx, event, effects)


async def _write_room_effects(
    session: AsyncSession,
    ctx: TenantCtx,
    event: PrEvent,
    effects: tuple[Effect, ...],
) -> None:
    """Write Switch checklist or intake failure to the transactional outbox."""
    room_effect = next(
        (
            effect
            for effect in (Effect.OUTBOX_THREAD_AND_CHECKLIST, Effect.OUTBOX_FAILED_RULES)
            if effect in effects
        ),
        None,
    )
    if room_effect is None or event.room_id is None or event.room_message is None:
        return
    await write_outbox(
        session,
        installation_id=ctx.installation_id,
        channel="switch",
        kind="room.message",
        payload={"channel_id": event.room_id, "message": event.room_message},
        idempotency_key=f"triage:{event.event_id}:{room_effect.value}",
    )
