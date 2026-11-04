"""Typed frames of the Switch agent WebSocket protocol and a tolerant parser."""

import json
from dataclasses import dataclass
from typing import cast

DOMAIN_EVENTS = frozenset({"message", "command", "room_join"})


@dataclass(frozen=True)
class ConnectionState:
    """First frame on a connection: the server's view of rooms, cursor and generation."""

    connection_id: str
    generation: int
    rooms: tuple[str, ...]
    cursor: int


@dataclass(frozen=True)
class Ping:
    """Heartbeat the client must answer with a pong before anything else."""


@dataclass(frozen=True)
class Gap:
    """The server could not replay every event since our cursor."""

    from_sequence: int
    resumed_at: int
    rooms: tuple[str, ...]
    all_rooms: bool


@dataclass(frozen=True)
class Evicted:
    """The server ended this connection; ``code`` says whether to reconnect."""

    code: str
    reason: str


@dataclass(frozen=True)
class Refused:
    """The server refused the connection at open."""

    status: int
    detail: str


@dataclass(frozen=True)
class AgentMessage:
    """A room message. ``body`` is untrusted data and must never be logged."""

    sequence: int
    room_id: str
    message_id: str
    sender: str
    sender_name: str
    body: str
    thread_id: str | None
    addressed: bool


@dataclass(frozen=True)
class OtherEvent:
    """A domain event we do not act on; kept only so the cursor moves past it."""

    sequence: int


Frame = ConnectionState | Ping | Gap | Evicted | Refused | AgentMessage | OtherEvent
QueueItem = AgentMessage | OtherEvent | Gap


def parse_frame(raw: str | bytes) -> Frame | None:
    """Decode one WebSocket text frame, returning None for anything unusable.

    Args:
        raw: The frame as received from the socket.

    Returns:
        A typed frame, or None when the frame is not JSON, is of an unknown kind, or lacks
        required fields. Unknown frames are ignored so protocol additions cannot crash us.
    """
    try:
        envelope = _as_object(json.loads(raw))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if envelope is None:
        return None
    kind = envelope.get("event") or envelope.get("type")
    data = _as_object(envelope.get("data")) or envelope
    if kind in DOMAIN_EVENTS:
        return _domain_event(str(kind), envelope, data)
    return _control_frame(kind, data)


def _control_frame(kind: object, data: dict[str, object]) -> Frame | None:
    """Build a control frame from its payload fields."""
    if kind == "ping":
        return Ping()
    if kind == "connection_state":
        return _connection_state(data)
    if kind == "gap":
        return _gap(data)
    if kind == "evicted":
        code = _text(data, "code")
        return Evicted(code, _text(data, "reason") or "") if code else None
    if kind == "refused":
        status = _integer(data, "status")
        return Refused(status, _text(data, "detail") or "") if status is not None else None
    return None


def _connection_state(data: dict[str, object]) -> ConnectionState | None:
    """Build the connection_state frame, or None when a required field is missing."""
    connection_id = _text(data, "connection_id")
    generation = _integer(data, "generation")
    cursor = _integer(data, "cursor")
    if not connection_id or generation is None or cursor is None:
        return None
    return ConnectionState(connection_id, generation, _rooms(data), cursor)


def _gap(data: dict[str, object]) -> Gap | None:
    """Build the gap frame, or None when a required field is missing."""
    resumed_at = _integer(data, "resumed_at")
    if resumed_at is None:
        return None
    from_sequence = _integer(data, "from_sequence")
    return Gap(
        from_sequence=from_sequence if from_sequence is not None else resumed_at,
        resumed_at=resumed_at,
        rooms=_rooms(data),
        all_rooms=data.get("all_rooms") is True,
    )


def _domain_event(
    kind: str, envelope: dict[str, object], data: dict[str, object]
) -> AgentMessage | OtherEvent | None:
    """Build a domain event; a malformed message degrades to OtherEvent."""
    sequence = _integer(data, "sequence")
    if sequence is None:
        sequence = _integer(envelope, "id")
    if sequence is None:
        return None
    if kind != "message":
        return OtherEvent(sequence)
    message = _message(sequence, data)
    return message if message is not None else OtherEvent(sequence)


def _message(sequence: int, data: dict[str, object]) -> AgentMessage | None:
    """Build an AgentMessage from a message event's payload."""
    payload = _as_object(data.get("payload"))
    room_id = _text(data, "room_id")
    if payload is None or not room_id:
        return None
    message_id = _text(payload, "message_id")
    sender = _text(payload, "sender")
    body = payload.get("body")
    if not message_id or not sender or not isinstance(body, str):
        return None
    return AgentMessage(
        sequence=sequence,
        room_id=room_id,
        message_id=message_id,
        sender=sender,
        sender_name=_text(payload, "sender_name") or "",
        body=body,
        thread_id=_text(payload, "thread_id") or None,
        addressed=payload.get("addressed") is True,
    )


def _rooms(data: dict[str, object]) -> tuple[str, ...]:
    """Return the sorted string room ids from ``data['rooms']``."""
    value = data.get("rooms")
    if not isinstance(value, list):
        return ()
    rooms = [room for room in cast(list[object], value) if isinstance(room, str)]
    return tuple(sorted(rooms))


def _as_object(value: object) -> dict[str, object] | None:
    """Return value as a JSON object, or None."""
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return None


def _text(data: dict[str, object], key: str) -> str | None:
    """Return a non-empty string field, or None."""
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def _integer(data: dict[str, object], key: str) -> int | None:
    """Return an integer field, rejecting booleans, or None."""
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
