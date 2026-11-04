"""Stream position shared by the socket reader and the event consumer."""

from heimdall.adapters.switch.agent.protocol import ConnectionState


class Progress:
    """Where we are in the event stream.

    Each field has exactly one writer, so no lock is needed under asyncio:

    * the reader task writes ``received``, ``generation`` and ``rooms``;
    * the consumer task writes ``processed``.

    ``processed`` is what we report in a pong, so a crash replays events instead of losing them.
    ``received`` is where a reconnect resumes, because events up to there are already queued.
    """

    def __init__(self) -> None:
        """Start with no position; the first ``connection_state`` seeds it."""
        self.received: int | None = None
        self.processed: int | None = None
        self.generation: int | None = None
        self.rooms: tuple[str, ...] = ()
        self.attaches = 0

    @property
    def pong_cursor(self) -> int:
        """Cursor to send in a pong: the highest sequence fully processed."""
        return self.processed if self.processed is not None else 0

    def begin(self, state: ConnectionState) -> None:
        """Adopt the server's answer from ``connection_state`` (reader only)."""
        self.attaches += 1
        self.generation = state.generation
        self.rooms = state.rooms
        if self.received is None:
            self.received = state.cursor
            self.processed = state.cursor

    def mark_received(self, sequence: int) -> None:
        """Record the highest sequence handed to the queue (reader only)."""
        self.received = sequence

    def mark_processed(self, sequence: int) -> None:
        """Record the highest sequence the handler finished (consumer only)."""
        self.processed = sequence
