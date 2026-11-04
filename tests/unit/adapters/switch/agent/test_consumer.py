"""Tests for the queue consumer: ordering, dedupe, filtering and cursor progress."""

import asyncio

import pytest

from heimdall.adapters.switch.agent._consumer import GapHandler, MessageHandler, consume
from heimdall.adapters.switch.agent._progress import Progress
from heimdall.adapters.switch.agent.protocol import AgentMessage, Gap, OtherEvent, QueueItem


def _message(sequence: int, *, addressed: bool = True, message_id: str = "") -> AgentMessage:
    return AgentMessage(
        sequence=sequence,
        room_id="r1",
        message_id=message_id or f"m{sequence}",
        sender="@bob:switch",
        sender_name="Bob",
        body="secret body",
        thread_id=None,
        addressed=addressed,
    )


async def _run(
    items: list[QueueItem],
    on_message: MessageHandler,
    on_gap: GapHandler | None = None,
    progress: Progress | None = None,
) -> Progress:
    progress = progress or Progress()
    queue: asyncio.Queue[QueueItem] = asyncio.Queue()
    for item in items:
        queue.put_nowait(item)
    task = asyncio.create_task(consume(queue, progress, on_message, on_gap, dedupe_size=10))
    await queue.join()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return progress


async def test_addressed_messages_reach_the_handler_in_order() -> None:
    seen: list[str] = []

    async def handler(message: AgentMessage) -> None:
        seen.append(message.message_id)

    progress = await _run([_message(1), _message(2), _message(3)], handler)

    assert seen == ["m1", "m2", "m3"]
    assert progress.pong_cursor == 3


async def test_unaddressed_messages_are_skipped_but_advance_the_cursor() -> None:
    seen: list[str] = []

    async def handler(message: AgentMessage) -> None:
        seen.append(message.message_id)

    progress = await _run([_message(1, addressed=False), OtherEvent(2)], handler)

    assert seen == []
    assert progress.pong_cursor == 2


async def test_replayed_message_id_is_handled_once() -> None:
    seen: list[int] = []

    async def handler(message: AgentMessage) -> None:
        seen.append(message.sequence)

    progress = await _run([_message(1, message_id="dup"), _message(2, message_id="dup")], handler)

    assert seen == [1]
    assert progress.pong_cursor == 2


async def test_handler_failure_is_isolated_and_logs_no_body(
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[str] = []

    async def handler(message: AgentMessage) -> None:
        if message.sequence == 1:
            raise RuntimeError("boom")
        seen.append(message.message_id)

    progress = await _run([_message(1), _message(2)], handler)

    assert seen == ["m2"]
    assert progress.pong_cursor == 2
    assert "secret body" not in capsys.readouterr().out


async def test_gap_resets_the_cursor_to_resumed_at_and_calls_the_gap_handler() -> None:
    gaps: list[Gap] = []

    async def handler(message: AgentMessage) -> None:
        return None

    async def on_gap(gap: Gap) -> None:
        gaps.append(gap)

    gap = Gap(from_sequence=3, resumed_at=40, rooms=("r1",), all_rooms=False)
    progress = await _run([_message(3), gap], handler, on_gap)

    assert gaps == [gap]
    assert progress.pong_cursor == 40
