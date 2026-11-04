"""A scriptable in-process Switch server for agent-connection tests."""

import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import Server, ServerConnection, serve

Script = Callable[["Session"], Awaitable[None]]


@dataclass
class Session:
    """One accepted connection: what the client asked for and what it sent back."""

    ws: ServerConnection
    index: int
    query: dict[str, str]
    headers: dict[str, str]
    pongs: list[int] = field(default_factory=lambda: list[int]())

    async def send(self, event: str, data: dict[str, object], **extra: object) -> None:
        await self.ws.send(json.dumps({"event": event, "data": data, **extra}))

    async def state(
        self, *, generation: int = 1, cursor: int = 0, rooms: list[str] | None = None
    ) -> None:
        await self.send(
            "connection_state",
            {
                "connection_id": self.query["connection_id"],
                "generation": generation,
                "rooms": rooms if rooms is not None else ["r1"],
                "cursor": cursor,
            },
        )

    async def message(self, sequence: int, *, addressed: bool = True, body: str = "hi") -> None:
        payload: dict[str, object] = {
            "addressed": addressed,
            "sender": "@bob:switch",
            "sender_name": "Bob",
            "message_id": f"m{sequence}",
            "body": body,
            "timestamp": 1,
            "thread_id": None,
            "attachments": [],
        }
        data: dict[str, object] = {
            "type": "message",
            "room_id": "r1",
            "payload": payload,
            "sequence": sequence,
        }
        await self.send("message", data, id=sequence)

    async def ping_and_wait_for_pong(self, timeout: float = 2.0) -> int:
        """Send a ping and return the cursor in the pong that answers it."""
        await self.send("ping", {})
        raw = await asyncio.wait_for(self.ws.recv(), timeout)
        pong = json.loads(raw)
        assert pong["type"] == "pong"
        self.pongs.append(pong["cursor"])
        return int(pong["cursor"])

    async def wait_closed(self) -> None:
        await self.ws.wait_closed()


class FakeSwitch:
    """Runs one script per connection, in connection order."""

    def __init__(self, scripts: list[Script]) -> None:
        self.scripts = scripts
        self.sessions: list[Session] = []
        self.connected: list[asyncio.Event] = [asyncio.Event() for _ in scripts]
        self.base_url = ""

    async def _handle(self, ws: ServerConnection) -> None:
        index = len(self.sessions)
        request = ws.request
        assert request is not None
        query = {k: v[0] for k, v in parse_qs(urlsplit(request.path).query).items()}
        headers = {k.lower(): v for k, v in request.headers.items()}
        session = Session(ws, index, query, headers)
        self.sessions.append(session)
        if index >= len(self.scripts):
            await ws.close(1011)
            return
        self.connected[index].set()
        await self.scripts[index](session)


@asynccontextmanager
async def running_switch(scripts: list[Script]) -> AsyncGenerator[FakeSwitch]:
    fake = FakeSwitch(scripts)
    server: Server = await serve(fake._handle, "127.0.0.1", 0)  # pyright: ignore[reportPrivateUsage]
    port = server.sockets[0].getsockname()[1]
    fake.base_url = f"http://127.0.0.1:{port}"
    try:
        yield fake
    finally:
        server.close()
        await server.wait_closed()
