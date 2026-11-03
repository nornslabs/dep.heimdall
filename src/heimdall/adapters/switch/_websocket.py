"""Mattermost WebSocket event parsing and reconnect loop."""

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from typing import Final, cast
from urllib.parse import urlsplit, urlunsplit

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from heimdall.adapters.switch.ports import RoomEvent

INITIAL_RECONNECT_SECONDS: Final = 1.0
MAX_RECONNECT_SECONDS: Final = 30.0


async def listen(base_url: str, token: str, stop: asyncio.Event) -> AsyncIterator[RoomEvent]:
    """Stream posts and reactions, reconnecting with capped exponential backoff."""
    delay = INITIAL_RECONNECT_SECONDS
    while not stop.is_set():
        try:
            async with connect(
                _websocket_url(base_url),
                additional_headers={"Authorization": f"Bearer {token}"},
                open_timeout=10,
                ping_interval=20,
                ping_timeout=20,
            ) as websocket:
                while not stop.is_set():
                    raw_event = await _receive_or_stop(websocket, stop)
                    if raw_event is None:
                        return
                    delay = INITIAL_RECONNECT_SECONDS
                    event = _parse_event(raw_event)
                    if event is not None:
                        yield event
        except (ConnectionClosed, OSError, TimeoutError):
            if stop.is_set():
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                delay = min(delay * 2, MAX_RECONNECT_SECONDS)


def _websocket_url(base_url: str) -> str:
    """Convert an HTTP server base URL to its Mattermost WebSocket endpoint."""
    parts = urlsplit(base_url.rstrip("/"))
    scheme = {"http": "ws", "https": "wss"}.get(parts.scheme)
    if scheme is None:
        raise ValueError("Switch URL must use http or https")
    return urlunsplit((scheme, parts.netloc, f"{parts.path.rstrip('/')}/api/v4/websocket", "", ""))


async def _receive_or_stop(websocket: ClientConnection, stop: asyncio.Event) -> str | bytes | None:
    """Wait for one event or stop promptly without polling the socket."""
    receive = asyncio.create_task(websocket.recv())
    stopping = asyncio.create_task(stop.wait())
    done, pending = await asyncio.wait({receive, stopping}, return_when=asyncio.FIRST_COMPLETED)
    if receive in pending:
        receive.cancel()
        await asyncio.gather(receive, return_exceptions=True)
    if stopping in pending:
        stopping.cancel()
        await asyncio.gather(stopping, return_exceptions=True)
    if stopping in done:
        return None
    return receive.result()


def _parse_event(raw_event: str | bytes) -> RoomEvent | None:
    """Parse supported Mattermost event envelopes without trusting their shape."""
    try:
        parsed: object = json.loads(raw_event)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    envelope = cast(dict[str, object], parsed)
    event_kind = envelope.get("event")
    data_value = envelope.get("data")
    if not isinstance(data_value, dict):
        return None
    data = cast(dict[str, object], data_value)
    if event_kind == "posted":
        post = _nested_object(data.get("post"))
        if post is None:
            return None
        return _post_event(post)
    if event_kind in {"reaction_added", "reaction_removed"}:
        reaction = _nested_object(data.get("reaction"))
        if reaction is None:
            return None
        return _reaction_event(str(event_kind), reaction)
    return None


def _nested_object(value: object) -> Mapping[str, object] | None:
    """Decode a JSON object encoded directly or as a string inside an event."""
    if isinstance(value, str):
        try:
            parsed: object = json.loads(value)
        except json.JSONDecodeError:
            return None
    else:
        parsed = value
    if not isinstance(parsed, dict):
        return None
    return cast(dict[str, object], parsed)


def _post_event(post: Mapping[str, object]) -> RoomEvent | None:
    """Build a post event when its identifiers have the expected types."""
    channel_id = post.get("channel_id")
    post_id = post.get("id")
    user_id = post.get("user_id")
    if (
        not isinstance(channel_id, str)
        or not isinstance(post_id, str)
        or not isinstance(user_id, str)
        or not channel_id
        or not post_id
        or not user_id
    ):
        return None
    root_id = post.get("root_id", "")
    message = post.get("message", "")
    return RoomEvent(
        kind="posted",
        channel_id=channel_id,
        post_id=post_id,
        user_id=user_id,
        root_id=root_id if isinstance(root_id, str) else "",
        message=message if isinstance(message, str) else "",
    )


def _reaction_event(kind: str, reaction: Mapping[str, object]) -> RoomEvent | None:
    """Build a reaction event when its identifiers have the expected types."""
    channel_id = reaction.get("channel_id", "")
    post_id = reaction.get("post_id")
    user_id = reaction.get("user_id")
    emoji_name = reaction.get("emoji_name")
    if (
        not isinstance(channel_id, str)
        or not isinstance(post_id, str)
        or not isinstance(user_id, str)
        or not isinstance(emoji_name, str)
    ):
        return None
    if not post_id or not user_id or not emoji_name:
        return None
    return RoomEvent(
        kind=kind,
        channel_id=channel_id,
        post_id=post_id,
        user_id=user_id,
        emoji_name=emoji_name,
    )
