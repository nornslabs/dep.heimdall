"""Tests for parsing Switch agent-protocol frames."""

import json

from heimdall.adapters.switch.agent.protocol import (
    AgentMessage,
    ConnectionState,
    Evicted,
    Gap,
    OtherEvent,
    Ping,
    Refused,
    parse_frame,
)


def _message_frame(sequence: int, **payload: object) -> str:
    body: dict[str, object] = {
        "addressed": True,
        "sender": "@bob:switch",
        "sender_name": "Bob",
        "message_id": f"m{sequence}",
        "body": "hello",
        "timestamp": 1,
        "thread_id": None,
        "attachments": [],
    }
    body.update(payload)
    data = {"type": "message", "room_id": "r1", "payload": body, "sequence": sequence}
    return json.dumps({"event": "message", "data": data, "id": sequence})


def test_connection_state_is_parsed_with_sorted_rooms() -> None:
    raw = json.dumps(
        {
            "event": "connection_state",
            "data": {
                "connection_id": "c1",
                "generation": 4,
                "rooms": ["r2", "r1"],
                "cursor": 17,
            },
        }
    )

    frame = parse_frame(raw)

    assert frame == ConnectionState("c1", 4, ("r1", "r2"), 17)


def test_addressed_message_is_parsed() -> None:
    frame = parse_frame(_message_frame(5, thread_id="t9"))

    assert frame == AgentMessage(
        sequence=5,
        room_id="r1",
        message_id="m5",
        sender="@bob:switch",
        sender_name="Bob",
        body="hello",
        thread_id="t9",
        addressed=True,
    )


def test_message_is_not_addressed_unless_flag_is_exactly_true() -> None:
    frame = parse_frame(_message_frame(6, addressed="yes"))

    assert isinstance(frame, AgentMessage)
    assert frame.addressed is False


def test_malformed_message_still_advances_the_cursor() -> None:
    frame = parse_frame(_message_frame(7, message_id=None))

    assert frame == OtherEvent(sequence=7)


def test_command_and_room_join_are_other_events() -> None:
    raw = json.dumps({"event": "room_join", "data": {"payload": {}, "sequence": 8}, "id": 8})

    assert parse_frame(raw) == OtherEvent(sequence=8)


def test_control_frames_are_parsed() -> None:
    ping = parse_frame(json.dumps({"event": "ping", "data": {}}))
    gap = parse_frame(
        json.dumps(
            {
                "event": "gap",
                "data": {
                    "from_sequence": 3,
                    "resumed_at": 40,
                    "rooms": ["r1"],
                    "all_rooms": False,
                    "reason": "retention",
                },
            }
        )
    )
    evicted = parse_frame(
        json.dumps({"event": "evicted", "data": {"code": "taken_over", "reason": "x"}})
    )
    refused = parse_frame(
        json.dumps({"event": "refused", "data": {"status": 409, "detail": "busy"}})
    )

    assert ping == Ping()
    assert gap == Gap(from_sequence=3, resumed_at=40, rooms=("r1",), all_rooms=False)
    assert evicted == Evicted(code="taken_over", reason="x")
    assert refused == Refused(status=409, detail="busy")


def test_unknown_or_invalid_frames_are_ignored() -> None:
    assert parse_frame("not json") is None
    assert parse_frame("[1, 2]") is None
    assert parse_frame(json.dumps({"event": "subscription_changed", "data": {}})) is None
    assert parse_frame(json.dumps({"event": "connection_state", "data": {"cursor": 1}})) is None
