"""One WebSocket session: reads frames, answers pings first, queues events for the consumer."""

import asyncio
import json
from enum import Enum

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException

from heimdall.adapters.switch.agent._progress import Progress
from heimdall.adapters.switch.agent.config import (
    OPEN_TIMEOUT_SECONDS,
    AgentConnectionConfig,
    websocket_url,
)
from heimdall.adapters.switch.agent.protocol import (
    AgentMessage,
    ConnectionState,
    Evicted,
    Gap,
    OtherEvent,
    Ping,
    QueueItem,
    Refused,
    parse_frame,
)
from heimdall.shared.errors import SwitchRequestFailed
from heimdall.shared.logging import get_logger

log = get_logger(__name__)

FINAL_EVICTIONS = frozenset({"taken_over", "closed"})
AUTH_FAILED_CLOSE = 4401
REFUSAL_CLOSE_RANGE = range(4000, 5000)
CLIENT_ERROR_STATUSES = range(400, 500)
RATE_LIMITED = 429


class Disposition(Enum):
    """What the supervisor should do after a session ends."""

    RESUME = "resume"
    STOP = "stop"


async def run_session(
    config: AgentConnectionConfig,
    progress: Progress,
    queue: asyncio.Queue[QueueItem],
    stop: asyncio.Event,
) -> Disposition:
    """Hold one connection until it ends, then say whether to reconnect.

    Pings are answered the moment they arrive, before any event is queued, and the reader never
    waits on a handler. If the queue is full the session ends with RESUME instead of blocking:
    ``progress.received`` only covers queued events, so the next connection replays the rest.

    Raises:
        SwitchRequestFailed: Bad key, or a refusal that retrying cannot fix.
        OSError: The server could not be reached (the supervisor backs off).
        TimeoutError: The handshake timed out (the supervisor backs off).
    """
    url = websocket_url(
        config, start_from=progress.received, expected_generation=progress.generation
    )
    try:
        async with connect(
            url,
            additional_headers={"Authorization": f"Bearer {config.api_key}"},
            open_timeout=OPEN_TIMEOUT_SECONDS,
            ping_interval=None,
        ) as websocket:
            return await _read_frames(websocket, progress, queue, stop)
    except ConnectionClosed as closed:
        return _after_close(closed.rcvd.code if closed.rcvd else None)
    except InvalidStatus as rejected:
        return _after_rejected_handshake(rejected.response.status_code)
    except WebSocketException:
        return Disposition.RESUME


async def _read_frames(
    websocket: ClientConnection,
    progress: Progress,
    queue: asyncio.Queue[QueueItem],
    stop: asyncio.Event,
) -> Disposition:
    """Read frames until the session ends."""
    while True:
        raw = await _receive_or_stop(websocket, stop)
        if raw is None:
            return Disposition.STOP
        frame = parse_frame(raw)
        if isinstance(frame, Ping):
            await websocket.send(json.dumps({"type": "pong", "cursor": progress.pong_cursor}))
        elif isinstance(frame, ConnectionState):
            progress.begin(frame)
        elif isinstance(frame, Evicted):
            return _after_eviction(frame)
        elif isinstance(frame, Refused):
            raise SwitchRequestFailed(f"Switch refused the connection ({frame.status})")
        elif isinstance(frame, AgentMessage | OtherEvent | Gap) and not _enqueue(
            frame, progress, queue
        ):
            return Disposition.RESUME


def _enqueue(item: QueueItem, progress: Progress, queue: asyncio.Queue[QueueItem]) -> bool:
    """Queue an item without waiting; return False when the queue is full."""
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        log.warning("switch_agent_queue_full", resume_from=progress.received)
        return False
    progress.mark_received(item.resumed_at if isinstance(item, Gap) else item.sequence)
    return True


def _after_eviction(frame: Evicted) -> Disposition:
    """Decide from an ``evicted`` frame; taken_over and closed are final."""
    log.warning("switch_agent_evicted", code=frame.code)
    return Disposition.STOP if frame.code in FINAL_EVICTIONS else Disposition.RESUME


def _after_close(code: int | None) -> Disposition:
    """Map a WebSocket close code to a decision or a permanent failure."""
    if code == AUTH_FAILED_CLOSE:
        raise SwitchRequestFailed("Switch rejected the agent API key")
    if code is not None and code in REFUSAL_CLOSE_RANGE:
        raise SwitchRequestFailed(f"Switch refused the connection ({code - 4000})")
    return Disposition.RESUME


def _after_rejected_handshake(status: int) -> Disposition:
    """Treat a client-error HTTP answer to the upgrade as permanent, others as outages."""
    if status in CLIENT_ERROR_STATUSES and status != RATE_LIMITED:
        raise SwitchRequestFailed(f"Switch rejected the connection ({status})")
    return Disposition.RESUME


async def _receive_or_stop(websocket: ClientConnection, stop: asyncio.Event) -> str | bytes | None:
    """Receive one frame, or return None as soon as ``stop`` is set."""
    receive = asyncio.create_task(websocket.recv())
    stopping = asyncio.create_task(stop.wait())
    await asyncio.wait({receive, stopping}, return_when=asyncio.FIRST_COMPLETED)
    stopping.cancel()
    if not receive.done():
        receive.cancel()
        await asyncio.gather(receive, stopping, return_exceptions=True)
        return None
    await asyncio.gather(stopping, return_exceptions=True)
    return receive.result()
