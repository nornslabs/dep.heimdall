"""Use cases of the conversation module."""

from datetime import datetime

from sqlalchemy import and_, text
from sqlmodel import col
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.modules.conversation.domain import (
    QUESTION_WINDOW,
    Admitted,
    Limited,
    QuestionDecision,
    QuestionOutcome,
    QuestionScope,
    decide_question,
)
from heimdall.modules.conversation.models import AgentQuestion
from heimdall.shared.context import TenantCtx

_LOCK_SCOPE = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")


async def admit_question_in_transaction(
    session: AsyncSession,
    ctx: TenantCtx,
    scope: QuestionScope,
    message_id: str,
    now: datetime,
) -> QuestionDecision:
    """Decide whether an agent may answer a question, and record it so the next one is counted.

    The scope is locked until the caller's transaction ends, so two questions from the same
    sender cannot both slip under the limit. A message Switch delivers again gets its first
    decision back and is not counted twice.

    Args:
        session: Session inside the caller's transaction; this function never commits.
        ctx: Tenant and request context; supplies installation_id.
        scope: The agent, sender and PR (or room) the question is counted against.
        message_id: Switch message that carried the question.
        now: The time of the question.

    Returns:
        ``Admitted`` when the agent may answer; ``Limited`` when it must not, with
        ``send_notice`` true for the first refusal in a window only.
    """
    await _lock(session, ctx, scope)
    window = await _window_rows(session, ctx, scope, now)
    earlier = await _earlier_row(session, ctx, scope, message_id)
    if earlier is not None:
        return _repeat(earlier, window, now)
    decision = _decide(window, now)
    session.add(_new_row(ctx, scope, message_id, now, decision))
    return decision


async def _lock(session: AsyncSession, ctx: TenantCtx, scope: QuestionScope) -> None:
    """Hold a transaction-scoped lock on this sender's allowance for this agent and PR."""
    where = scope.pr_id if scope.pr_id is not None else f"room:{scope.room_id}"
    key = f"{ctx.installation_id}:{scope.agent.value}:{scope.sender.casefold()}:{where}"
    connection = await session.connection()
    await connection.execute(_LOCK_SCOPE, {"key": key})


async def _window_rows(
    session: AsyncSession, ctx: TenantCtx, scope: QuestionScope, now: datetime
) -> list[AgentQuestion]:
    """Read this scope's questions that still count, oldest first."""
    in_scope = (
        col(AgentQuestion.pr_id) == scope.pr_id
        if scope.pr_id is not None
        else and_(col(AgentQuestion.pr_id).is_(None), col(AgentQuestion.room_id) == scope.room_id)
    )
    result = await session.exec(
        select_for_tenant(AgentQuestion, ctx.installation_id)
        .where(
            AgentQuestion.agent == scope.agent.value,
            AgentQuestion.sender == scope.sender.casefold(),
            col(AgentQuestion.asked_at) > now - QUESTION_WINDOW,
            in_scope,
        )
        .order_by(col(AgentQuestion.asked_at))
    )
    return list(result.all())


async def _earlier_row(
    session: AsyncSession, ctx: TenantCtx, scope: QuestionScope, message_id: str
) -> AgentQuestion | None:
    """Find the row saved when this message was first delivered, if it was."""
    result = await session.exec(
        select_for_tenant(AgentQuestion, ctx.installation_id).where(
            AgentQuestion.agent == scope.agent.value,
            AgentQuestion.message_id == message_id,
        )
    )
    return result.first()


def _answered_times(window: list[AgentQuestion]) -> list[datetime]:
    return [row.asked_at for row in window if row.outcome == QuestionOutcome.ANSWERED]


def _notice_sent_since_last_answer(window: list[AgentQuestion]) -> bool:
    """Whether the current run of refusals has already had its one notice."""
    answered = _answered_times(window)
    if not answered:
        return False
    last_answer = max(answered)
    return any(
        row.outcome == QuestionOutcome.LIMITED and row.asked_at >= last_answer for row in window
    )


def _decide(window: list[AgentQuestion], now: datetime) -> QuestionDecision:
    return decide_question(_answered_times(window), _notice_sent_since_last_answer(window), now)


def _repeat(earlier: AgentQuestion, window: list[AgentQuestion], now: datetime) -> QuestionDecision:
    """Answer a redelivered message as before; a repeated refusal never sends a second notice.

    The first delivery queued the notice in the transaction that saved its row, so a repeat
    has nothing left to send.
    """
    if earlier.outcome == QuestionOutcome.ANSWERED:
        return Admitted()
    decision = decide_question(_answered_times(window), True, now)
    if not isinstance(decision, Limited):
        return Limited(retry_after=now, send_notice=False)
    return decision


def _new_row(
    ctx: TenantCtx,
    scope: QuestionScope,
    message_id: str,
    now: datetime,
    decision: QuestionDecision,
) -> AgentQuestion:
    return AgentQuestion(
        installation_id=ctx.installation_id,
        agent=scope.agent.value,
        sender=scope.sender.casefold(),
        pr_id=scope.pr_id,
        room_id=scope.room_id,
        message_id=message_id,
        outcome=_outcome_of(decision).value,
        asked_at=now,
    )


def _outcome_of(decision: QuestionDecision) -> QuestionOutcome:
    if isinstance(decision, Admitted):
        return QuestionOutcome.ANSWERED
    return QuestionOutcome.LIMITED if decision.send_notice else QuestionOutcome.LIMITED_SILENT
