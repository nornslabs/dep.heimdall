"""Consumer task: runs handlers off the socket so slow work never starves heartbeats."""

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from heimdall.adapters.switch.agent._progress import Progress
from heimdall.adapters.switch.agent.protocol import AgentMessage, Gap, QueueItem
from heimdall.shared.logging import get_logger

log = get_logger(__name__)

MessageHandler = Callable[[AgentMessage], Awaitable[None]]
GapHandler = Callable[[Gap], Awaitable[None]]


class _RecentIds:
    """Bounded memory of recent message ids, used to drop replays after a reconnect."""

    def __init__(self, size: int) -> None:
        self._size = size
        self._ids: OrderedDict[str, None] = OrderedDict()

    def seen_before(self, message_id: str) -> bool:
        """Return True if the id was already recorded; otherwise record it."""
        if message_id in self._ids:
            return True
        self._ids[message_id] = None
        if len(self._ids) > self._size:
            self._ids.popitem(last=False)
        return False


async def consume(
    queue: asyncio.Queue[QueueItem],
    progress: Progress,
    on_message: MessageHandler,
    on_gap: GapHandler | None,
    *,
    dedupe_size: int,
) -> None:
    """Process queued items one at a time, in order, until cancelled.

    A failing handler is logged by identifier only (never the body) and the cursor still
    advances, matching how the Mattermost event loop treats handler errors.

    Args:
        queue: Items put there by the socket reader.
        progress: Updated after each item so pongs report the processed cursor.
        on_message: Called for each new addressed message.
        on_gap: Called when the server reports a gap; may be None.
        dedupe_size: How many recent message ids to remember.
    """
    recent = _RecentIds(dedupe_size)
    while True:
        item = await queue.get()
        try:
            await _handle(item, progress, on_message, on_gap, recent)
        finally:
            queue.task_done()


async def _handle(
    item: QueueItem,
    progress: Progress,
    on_message: MessageHandler,
    on_gap: GapHandler | None,
    recent: _RecentIds,
) -> None:
    """Dispatch one item and move the processed cursor past it."""
    if isinstance(item, Gap):
        await _call_gap(item, on_gap)
        progress.mark_processed(item.resumed_at)
        return
    if (
        isinstance(item, AgentMessage)
        and item.addressed
        and not recent.seen_before(item.message_id)
    ):
        await _call_message(item, on_message)
    progress.mark_processed(item.sequence)


async def _call_message(message: AgentMessage, on_message: MessageHandler) -> None:
    """Run the message handler, logging ids only if it fails."""
    try:
        await on_message(message)
    except Exception as error:
        log.error(
            "switch_agent_handler_failed",
            room_id=message.room_id,
            message_id=message.message_id,
            error_type=type(error).__name__,
        )


async def _call_gap(gap: Gap, on_gap: GapHandler | None) -> None:
    """Run the gap handler, if any, logging the error type only if it fails."""
    if on_gap is None:
        return
    try:
        await on_gap(gap)
    except Exception as error:
        log.error("switch_agent_gap_handler_failed", error_type=type(error).__name__)
