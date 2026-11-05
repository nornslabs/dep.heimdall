"""Delivers due outbox messages to their channel and records the outcome."""

import asyncio
from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.columns import utc_now
from heimdall.adapters.db.outbox._alert import render_action_alert, render_dead_alert
from heimdall.adapters.db.outbox._retry import Dead, after_failure
from heimdall.adapters.db.outbox.models import OutboxMessage
from heimdall.adapters.db.outbox.writer import PENDING, write_outbox
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.shared.errors import HeimdallError
from heimdall.shared.ids import InstallationId
from heimdall.shared.logging import get_logger

SENT = "sent"
DEAD = "dead"
ALERT_CHANNEL = "switch"
ALERT_KIND = "alert.outbox_dead"
OUTBOX_POLL_SECONDS = 1.0
OUTBOX_BATCH_SIZE = 20

log = get_logger(__name__)


class ChannelSender(Protocol):
    """Delivers one outbox message to the outside world (GitHub, Switch)."""

    async def send(self, message: OutboxMessage) -> None:
        """Deliver ``message``; raise on failure. Must be safe to repeat for the same key."""
        ...


async def send_due_messages(
    sessions: async_sessionmaker[AsyncSession],
    senders: Mapping[str, ChannelSender],
    *,
    now: datetime,
    batch: int = OUTBOX_BATCH_SIZE,
) -> int:
    """Send up to ``batch`` messages that are due, each in its own transaction.

    Messages whose channel has no sender are left pending for a worker that has one.

    Args:
        sessions: Session factory for the outbox's database.
        senders: Channel name to sender.
        now: The current time; messages due at or before it are sent.
        batch: Most messages to handle in this call.

    Returns:
        How many messages were handled (sent, rescheduled or marked dead).
    """
    handled = 0
    while handled < batch and await _send_next(sessions, senders, now):
        handled += 1
    return handled


async def run_outbox_loop(
    sessions: async_sessionmaker[AsyncSession],
    senders: Mapping[str, ChannelSender],
    stop: asyncio.Event,
) -> None:
    """Keep sending due messages until ``stop`` is set, polling every second when idle.

    Args:
        sessions: Session factory for the outbox's database.
        senders: Channel name to sender.
        stop: Set it to end the loop after the current batch.
    """
    while not stop.is_set():
        handled = await _send_batch_logging_errors(sessions, senders)
        if handled < OUTBOX_BATCH_SIZE:
            await _wait_or_stop(stop, OUTBOX_POLL_SECONDS)


async def _send_batch_logging_errors(
    sessions: async_sessionmaker[AsyncSession], senders: Mapping[str, ChannelSender]
) -> int:
    """Run one batch; a database hiccup is logged instead of stopping the worker."""
    try:
        return await send_due_messages(sessions, senders, now=utc_now())
    except Exception as error:
        log.error("outbox_batch_failed", error_type=type(error).__name__)
        return 0


async def _wait_or_stop(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        return


async def _send_next(
    sessions: async_sessionmaker[AsyncSession],
    senders: Mapping[str, ChannelSender],
    now: datetime,
) -> bool:
    """Lock, send and record the next due message.

    The row stays locked while it is sent, so two workers never send the same message.

    Returns:
        False when nothing was due.
    """
    async with unit_of_work(sessions) as session:
        message = await _lock_next_due(session, list(senders), now)
        if message is None:
            return False
        try:
            await senders[message.channel].send(message)
        except Exception as error:
            await _record_failure(session, message, error, now)
        else:
            _record_success(message, now)
        session.add(message)
        return True


async def _lock_next_due(
    session: AsyncSession, channels: list[str], now: datetime
) -> OutboxMessage | None:
    statement = (
        select(OutboxMessage)
        .where(OutboxMessage.status == PENDING)
        .where(OutboxMessage.next_attempt_at <= now)
        .where(col(OutboxMessage.channel).in_(channels))
        .order_by(col(OutboxMessage.next_attempt_at))
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    rows = await session.exec(statement)
    return rows.first()


def _record_success(message: OutboxMessage, now: datetime) -> None:
    message.status = SENT
    message.sent_at = now
    log.info("outbox_sent", outbox_id=str(message.id), channel=message.channel)


async def _record_failure(
    session: AsyncSession, message: OutboxMessage, error: Exception, now: datetime
) -> None:
    """Reschedule the message, or mark it dead and alert the room."""
    retryable = error.retryable if isinstance(error, HeimdallError) else True
    decision = after_failure(message.attempts, retryable=retryable, now=now)
    message.attempts += 1
    # Only the error type is stored and logged: messages can quote PR content.
    message.last_error = type(error).__name__
    log.warning(
        "outbox_send_failed",
        outbox_id=str(message.id),
        channel=message.channel,
        error_type=message.last_error,
        attempts=message.attempts,
    )
    if isinstance(decision, Dead):
        message.status = DEAD
        await _alert_room(session, message)
        return
    message.next_attempt_at = decision.at


async def _alert_room(session: AsyncSession, message: OutboxMessage) -> None:
    """Tell the dead message's room it died, unless it was itself an alert (no alert loops).

    Only room messages carry a ``channel_id``; a dead GitHub comment has no room to tell yet,
    so it is logged and the maintainers find it through the outbox row.
    """
    if message.kind == ALERT_KIND:
        return
    notify = _notify_block(message)
    if notify is not None:
        await _alert_pr_thread(session, message, notify)
        return
    room_id = message.payload.get("channel_id")
    if not isinstance(room_id, str) or not room_id:
        log.warning("outbox_alert_skipped", outbox_id=str(message.id), channel=message.channel)
        return
    await write_outbox(
        session,
        installation_id=InstallationId(message.installation_id),
        channel=ALERT_CHANNEL,
        kind=ALERT_KIND,
        payload={
            "channel_id": room_id,
            "message": render_dead_alert(
                str(message.id), message.channel, message.kind, message.last_error
            ),
            "outbox_id": str(message.id),
        },
        idempotency_key=f"alert:{message.id}",
    )


def _notify_block(message: OutboxMessage) -> dict[str, object] | None:
    """Return the room, PR and action a GitHub row asked to be told about, if it named them."""
    notify = message.payload.get("notify")
    if not isinstance(notify, dict):
        return None
    fields: dict[str, object] = dict(notify)  # pyright: ignore[reportUnknownArgumentType]
    if not isinstance(fields.get("channel_id"), str) or not fields["channel_id"]:
        return None
    return fields


async def _alert_pr_thread(
    session: AsyncSession, message: OutboxMessage, notify: dict[str, object]
) -> None:
    """Tell the PR's thread, as the agent that asked, that GitHub refused an approved action."""
    action = notify.get("action")
    payload: dict[str, object] = {
        "channel_id": notify["channel_id"],
        "message": render_action_alert(action if isinstance(action, str) else ""),
        "outbox_id": str(message.id),
    }
    for field in ("agent", "pr_id"):
        if isinstance(notify.get(field), str):
            payload[field] = notify[field]
    await write_outbox(
        session,
        installation_id=InstallationId(message.installation_id),
        channel=ALERT_CHANNEL,
        kind=ALERT_KIND,
        payload=payload,
        idempotency_key=f"alert:{message.id}",
    )
