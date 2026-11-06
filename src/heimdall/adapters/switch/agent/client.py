"""SwitchAgentClient: one agent's connection, event delivery and posting."""

import asyncio

import httpx

from heimdall.adapters.switch.agent._consumer import GapHandler, MessageHandler, consume
from heimdall.adapters.switch.agent._post import AgentPoster
from heimdall.adapters.switch.agent._progress import Progress
from heimdall.adapters.switch.agent._session import Disposition, run_session
from heimdall.adapters.switch.agent.config import AgentConnectionConfig
from heimdall.adapters.switch.agent.protocol import QueueItem
from heimdall.shared.logging import get_logger

log = get_logger(__name__)


class SwitchAgentClient:
    """Speaks the Switch agent protocol for one agent in one process.

    Concurrency model: a reader task owns the socket, answers pings and fills a bounded queue;
    a consumer task runs the handlers one at a time. A slow handler therefore cannot cause a
    heartbeat eviction. Run at most one client per agent id: a second process reusing the agent
    would take the connection over and this one would stop with ``taken_over``.

    Delivery is at-least-once with replays dropped by message id.
    """

    def __init__(
        self,
        config: AgentConnectionConfig,
        on_message: MessageHandler,
        on_gap: GapHandler | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Create the client; nothing connects until ``run``.

        Args:
            config: Connection settings for this agent.
            on_message: Called, one at a time and in order, for each new addressed message.
            on_gap: Called when Switch cannot replay everything since our cursor.
            transport: Optional HTTP transport for isolated tests.
        """
        self._config = config
        self._on_message = on_message
        self._on_gap = on_gap
        self._poster = AgentPoster(config, transport=transport)
        self._progress = Progress()
        self._running = False

    async def run(self, stop: asyncio.Event) -> None:
        """Hold the connection, reconnecting as needed, until ``stop`` or a final eviction.

        Args:
            stop: Set to shut down; queued events get a short drain before handlers are cancelled.

        Raises:
            RuntimeError: ``run`` is already active on this client.
            SwitchRequestFailed: Bad API key or a refusal that retrying cannot fix.
        """
        if self._running:
            raise RuntimeError("SwitchAgentClient.run is already active")
        self._running = True
        queue: asyncio.Queue[QueueItem] = asyncio.Queue(maxsize=self._config.queue_size)
        consumer = asyncio.create_task(
            consume(
                queue,
                self._progress,
                self._on_message,
                self._on_gap,
                dedupe_size=self._config.dedupe_size,
            )
        )
        supervisor = asyncio.create_task(self._supervise(queue, stop))
        try:
            await self._wait_for_end(supervisor, consumer, queue)
        finally:
            for task in (supervisor, consumer):
                task.cancel()
            await asyncio.gather(supervisor, consumer, return_exceptions=True)
            self._running = False

    async def post_message(self, room_id: str, content: str) -> None:
        """Post ``content`` in ``room_id`` as this agent (see ``AgentPoster.post``)."""
        await self._poster.post(room_id, content)

    async def send_typing(self, channel_id: str) -> None:
        """Send a typing indicator in ``channel_id`` as this agent."""
        await self._poster.send_typing(channel_id)

    async def add_reaction(self, post_id: str, emoji_name: str) -> None:
        """Add a reaction to ``post_id`` as this agent."""
        await self._poster.add_reaction(post_id, emoji_name)

    async def remove_reaction(self, post_id: str, emoji_name: str) -> None:
        """Remove a reaction from ``post_id`` as this agent."""
        await self._poster.remove_reaction(post_id, emoji_name)

    async def post_progress(
        self,
        channel_id: str,
        thread_id: str | None,
        text: str,
        *,
        update_post_id: str | None = None,
    ) -> str | None:
        """Post or update a progress message (see ``AgentPoster.post_progress``)."""
        return await self._poster.post_progress(
            channel_id, thread_id, text, update_post_id=update_post_id
        )

    async def post_rich(
        self,
        room_id: str,
        content: str,
        thread_id: str | None,
        *,
        attachments: list[dict[str, object]],
    ) -> str | None:
        """Post a rich message with attachments (see ``AgentPoster.post_rich``)."""
        return await self._poster.post_rich(room_id, content, thread_id, attachments=attachments)

    async def aclose(self) -> None:
        """Close the HTTP client."""
        await self._poster.aclose()

    async def _wait_for_end(
        self,
        supervisor: asyncio.Task[None],
        consumer: asyncio.Task[None],
        queue: asyncio.Queue[QueueItem],
    ) -> None:
        """Wait for the supervisor to finish, surfacing its error or a consumer crash."""
        await asyncio.wait({supervisor, consumer}, return_when=asyncio.FIRST_COMPLETED)
        if consumer.done():
            consumer.result()
            raise RuntimeError("Switch agent consumer stopped unexpectedly")
        supervisor.result()
        try:
            await asyncio.wait_for(queue.join(), self._config.drain_timeout_seconds)
        except TimeoutError:
            log.warning("switch_agent_drain_timeout", pending=queue.qsize())

    async def _supervise(self, queue: asyncio.Queue[QueueItem], stop: asyncio.Event) -> None:
        """Run sessions one after another with capped exponential backoff."""
        delay = self._config.initial_backoff_seconds
        while not stop.is_set():
            attaches_before = self._progress.attaches
            try:
                disposition = await run_session(self._config, self._progress, queue, stop)
            except (OSError, TimeoutError):
                disposition = Disposition.RESUME
            if disposition is Disposition.STOP:
                return
            if self._progress.attaches > attaches_before:
                delay = self._config.initial_backoff_seconds
            if await _sleep_or_stop(delay, stop):
                return
            delay = min(delay * 2, self._config.max_backoff_seconds)


async def _sleep_or_stop(seconds: float, stop: asyncio.Event) -> bool:
    """Wait ``seconds``; return True if ``stop`` was set first."""
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        return False
    return True
