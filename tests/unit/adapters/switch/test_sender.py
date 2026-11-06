"""Tests for Switch Rooms outbox delivery."""

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest
from websockets.asyncio.server import ServerConnection, serve

from heimdall.adapters.db.outbox.models import OutboxMessage
from heimdall.adapters.switch import _websocket
from heimdall.adapters.switch.ports import RoomEvent
from heimdall.adapters.switch.sender import SwitchSender
from heimdall.shared.errors import SwitchRequestFailed, SwitchUnavailable


def _message(payload: dict[str, object]) -> OutboxMessage:
    """Build the outbox row fields required by the sender."""
    now = datetime.now(UTC)
    return OutboxMessage(
        installation_id=123,
        channel="switch",
        kind="switch.post",
        payload=payload,
        idempotency_key="outbox-key-1",
        status="pending",
        attempts=0,
        next_attempt_at=now,
        created_at=now,
    )


async def test_sender_posts_message_with_thread_and_idempotency_metadata() -> None:
    """Post body, thread parent, bearer token and outbox key are sent."""
    requests: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"id": "post-1"})

    sender = SwitchSender(
        "http://switch.local/",
        "bot-token",
        transport=httpx.MockTransport(handle),
    )
    try:
        await sender.send(
            _message({"channel_id": "room-1", "message": "ready", "root_id": "thread-1"})
        )
    finally:
        await sender.aclose()

    assert len(requests) == 1
    request = requests[0]
    assert request.url == "http://switch.local/api/v4/posts"
    assert request.headers["Authorization"] == "Bearer bot-token"
    assert json.loads(request.content) == {
        "channel_id": "room-1",
        "message": "ready",
        "root_id": "thread-1",
        "props": {"heimdall_outbox_id": "outbox-key-1"},
    }


@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_sender_maps_client_errors_as_permanent(status: int) -> None:
    """A rejected post is not retried unchanged."""
    sender = SwitchSender(
        "http://switch.local",
        "bot-token",
        transport=httpx.MockTransport(lambda _: httpx.Response(status)),
    )
    try:
        with pytest.raises(SwitchRequestFailed):
            await sender.send(_message({"channel_id": "room-1", "message": "ready"}))
    finally:
        await sender.aclose()


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_sender_maps_transient_status_as_retryable(status: int) -> None:
    """Rate limits and server errors are retried by the outbox."""
    sender = SwitchSender(
        "http://switch.local",
        "bot-token",
        transport=httpx.MockTransport(lambda _: httpx.Response(status)),
    )
    try:
        with pytest.raises(SwitchUnavailable):
            await sender.send(_message({"channel_id": "room-1", "message": "ready"}))
    finally:
        await sender.aclose()


async def test_sender_rejects_invalid_payload_before_network_call() -> None:
    """Malformed outbox payloads fail permanently without issuing HTTP."""
    sender = SwitchSender(
        "http://switch.local",
        "bot-token",
        transport=httpx.MockTransport(lambda _: pytest.fail("unexpected HTTP request")),
    )
    try:
        with pytest.raises(SwitchRequestFailed):
            await sender.send(_message({"channel_id": "room-1"}))
    finally:
        await sender.aclose()


async def test_room_port_opens_threads_mentions_roles_and_reacts() -> None:
    """Room operations use their expected REST paths and payloads."""
    requests: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v4/posts":
            return httpx.Response(201, json={"id": f"post-{len(requests)}"})
        if request.url.path == "/api/v4/users/me":
            return httpx.Response(200, json={"id": "user-1"})
        if request.url.path == "/api/v4/reactions":
            return httpx.Response(200, json={})
        return httpx.Response(200, json={})

    sender = SwitchSender(
        "http://switch.local",
        "bot-token",
        transport=httpx.MockTransport(handle),
    )
    try:
        root_id = await sender.open_thread("room-1", "PR #12 opened")
        reply_id = await sender.post_to_thread("room-1", root_id, "Review started")
        await sender.post_to_role("room-1", "Huginn", "Please reproduce")
        await sender.react(reply_id, "white_check_mark")
    finally:
        await sender.aclose()

    assert root_id == "post-1"
    assert reply_id == "post-2"
    assert [request.url.path for request in requests] == [
        "/api/v4/posts",
        "/api/v4/posts",
        "/api/v4/posts",
        "/api/v4/users/me",
        "/api/v4/reactions",
    ]
    assert json.loads(requests[1].content)["root_id"] == root_id
    assert json.loads(requests[2].content)["message"] == "@Huginn Please reproduce"
    assert json.loads(requests[4].content) == {
        "user_id": "user-1",
        "post_id": "post-2",
        "emoji_name": "white_check_mark",
    }


async def test_room_port_fetches_reacted_post_and_user() -> None:
    """Load the approval marker and reactor login through Switch REST."""
    requests: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v4/posts/post-1":
            return httpx.Response(
                200,
                json={
                    "id": "post-1",
                    "channel_id": "room-1",
                    "root_id": "root-1",
                    "message": "draft body",
                },
            )
        return httpx.Response(200, json={"username": "reviewer"})

    sender = SwitchSender(
        "http://switch.local",
        "bot-token",
        transport=httpx.MockTransport(handle),
    )
    try:
        post = await sender.get_post("post-1")
        username = await sender.get_username("user-1")
    finally:
        await sender.aclose()

    assert post.id == "post-1"
    assert post.channel_id == "room-1"
    assert post.root_id == "root-1"
    assert post.message == "draft body"
    assert username == "reviewer"
    assert [request.url.path for request in requests] == [
        "/api/v4/posts/post-1",
        "/api/v4/users/user-1",
    ]


async def test_room_port_listens_and_reconnects_for_posts_and_reactions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WebSocket reconnects after a disconnect and yields supported room events."""
    monkeypatch.setattr(_websocket, "INITIAL_RECONNECT_SECONDS", 0.01)
    connections = 0
    auth_headers: list[str | None] = []

    async def handle(websocket: ServerConnection) -> None:
        nonlocal connections
        connections += 1
        request = websocket.request
        auth_headers.append(request.headers.get("Authorization") if request is not None else None)
        if connections == 1:
            return
        await websocket.send(
            json.dumps(
                {
                    "event": "posted",
                    "data": {
                        "post": json.dumps(
                            {
                                "id": "post-1",
                                "channel_id": "room-1",
                                "user_id": "user-1",
                                "message": "@heimdall check this",
                                "root_id": "",
                            }
                        )
                    },
                }
            )
        )
        await websocket.send(
            json.dumps(
                {
                    "event": "reaction_added",
                    "data": {
                        "reaction": json.dumps(
                            {
                                "channel_id": "room-1",
                                "post_id": "post-1",
                                "user_id": "user-1",
                                "emoji_name": "+1",
                            }
                        )
                    },
                }
            )
        )

    stop = asyncio.Event()
    async with serve(handle, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        sender = SwitchSender(f"http://127.0.0.1:{port}", "bot-token")
        events: list[RoomEvent] = []
        try:
            async for event in sender.listen(stop):
                events.append(event)
                if len(events) == 2:
                    stop.set()
        finally:
            await sender.aclose()

    assert connections == 2
    assert auth_headers == ["Bearer bot-token", "Bearer bot-token"]
    assert [event.kind for event in events] == ["posted", "reaction_added"]
    assert events[0].message == "@heimdall check this"
    assert events[1].emoji_name == "+1"
