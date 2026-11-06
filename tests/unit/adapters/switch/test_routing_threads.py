"""Tests for routing agent posts into the PR's thread."""

from datetime import UTC, datetime

import pytest

from heimdall.adapters.db.columns import JsonObject
from heimdall.adapters.db.outbox.models import OutboxMessage
from heimdall.adapters.switch.routing import SwitchRoutingSender
from heimdall.shared.agents import AgentName
from heimdall.shared.errors import SwitchUnavailable

PR_ID = "11111111-1111-1111-1111-111111111111"
INSTALLATION_ID = 123


class ThreadedPoster:
    """Stands in for one agent's poster; hands out event ids and records every post."""

    def __init__(self, event_id: str = "sw_new") -> None:
        self.threaded: list[tuple[str, str | None]] = []
        self.plain: list[tuple[str, str]] = []
        self.rich: list[tuple[str, str, str | None, list[dict[str, object]]]] = []
        self._event_id = event_id

    async def post(self, room_id: str, content: str) -> None:
        self.plain.append((room_id, content))

    async def post_in_thread(self, content: str, thread_id: str | None) -> str:
        self.threaded.append((content, thread_id))
        return self._event_id

    async def post_rich(
        self,
        room_id: str,
        content: str,
        thread_id: str | None,
        *,
        attachments: list[dict[str, object]],
    ) -> str | None:
        self.rich.append((room_id, content, thread_id, attachments))
        return self._event_id


class RichPosterReturnsNone(ThreadedPoster):
    """A poster whose Mattermost bot is missing, so rich posts cannot render."""

    async def post_rich(
        self,
        room_id: str,
        content: str,
        thread_id: str | None,
        *,
        attachments: list[dict[str, object]],
    ) -> str | None:
        self.rich.append((room_id, content, thread_id, attachments))
        return None


class FakeThreads:
    """In-memory stand-in for the PR thread store."""

    def __init__(self, known: dict[str, str] | None = None) -> None:
        self.known = dict(known or {})

    async def get(self, installation_id: int, pr_id: str) -> str | None:
        assert installation_id == INSTALLATION_ID
        return self.known.get(pr_id)

    async def save(self, installation_id: int, pr_id: str, thread_id: str) -> None:
        assert installation_id == INSTALLATION_ID
        self.known[pr_id] = thread_id


def _message(payload: JsonObject) -> OutboxMessage:
    now = datetime.now(UTC)
    return OutboxMessage(
        installation_id=INSTALLATION_ID,
        channel="switch",
        kind="room.message",
        payload=payload,
        idempotency_key="outbox-key-1",
        status="pending",
        attempts=0,
        next_attempt_at=now,
        created_at=now,
    )


def _post(
    agent: str,
    text: str,
    *,
    root: bool = False,
    pr_id: str | None = PR_ID,
    attachments: list[dict[str, object]] | None = None,
) -> JsonObject:
    payload: JsonObject = {"agent": agent, "channel_id": "room-1", "message": text}
    if pr_id is not None:
        payload["pr_id"] = pr_id
    if root:
        payload["thread_role"] = "root"
    if attachments is not None:
        payload["attachments"] = attachments
    return payload


def _router(poster: ThreadedPoster, threads: FakeThreads) -> SwitchRoutingSender:
    return SwitchRoutingSender(None, {AgentName.HEIMDALL: poster, AgentName.LOKI: poster}, threads)


async def test_the_root_post_opens_the_thread_and_saves_its_id() -> None:
    poster, threads = ThreadedPoster("sw_root"), FakeThreads()

    await _router(poster, threads).send(_message(_post("heimdall", "checklist", root=True)))

    assert poster.threaded == [("checklist", None)]
    assert threads.known == {PR_ID: "sw_root"}


async def test_a_reply_goes_into_the_saved_thread() -> None:
    poster, threads = ThreadedPoster(), FakeThreads({PR_ID: "sw_root"})

    await _router(poster, threads).send(_message(_post("loki", "no findings")))

    assert poster.threaded == [("no findings", "sw_root")]
    assert threads.known == {PR_ID: "sw_root"}


async def test_a_second_checklist_replies_in_the_thread_instead_of_opening_another() -> None:
    poster, threads = ThreadedPoster(), FakeThreads({PR_ID: "sw_root"})

    await _router(poster, threads).send(_message(_post("heimdall", "checklist 2", root=True)))

    assert poster.threaded == [("checklist 2", "sw_root")]
    assert threads.known == {PR_ID: "sw_root"}


async def test_a_reply_waits_when_the_thread_is_not_open_yet() -> None:
    poster, threads = ThreadedPoster(), FakeThreads()

    with pytest.raises(SwitchUnavailable):
        await _router(poster, threads).send(_message(_post("loki", "no findings")))

    assert poster.threaded == []


async def test_a_post_with_no_pr_id_keeps_the_flat_agent_path() -> None:
    poster, threads = ThreadedPoster(), FakeThreads()

    await _router(poster, threads).send(_message(_post("loki", "hello", pr_id=None)))

    assert poster.plain == [("room-1", "hello")]
    assert poster.threaded == []
    assert threads.known == {}


async def test_a_blank_pr_id_is_treated_like_no_pr_id() -> None:
    poster, threads = ThreadedPoster(), FakeThreads()

    await _router(poster, threads).send(_message(_post("loki", "hello", pr_id="")))

    assert poster.plain == [("room-1", "hello")]
    assert poster.threaded == []


async def test_a_rich_root_post_renders_the_card_and_opens_the_thread() -> None:
    poster, threads = ThreadedPoster("sw_root"), FakeThreads()

    await _router(
        poster,
        threads,
    ).send(
        _message(
            _post(
                "heimdall",
                "Card ready",
                root=True,
                attachments=[{"title": "Card ready", "color": "#16a34a"}],
            )
        )
    )

    assert poster.rich == [
        ("room-1", "Card ready", None, [{"title": "Card ready", "color": "#16a34a"}])
    ]
    assert poster.threaded == []
    assert threads.known == {PR_ID: "sw_root"}


async def test_a_rich_reply_renders_the_card_in_the_saved_thread() -> None:
    poster, threads = ThreadedPoster("sw_root"), FakeThreads({PR_ID: "sw_root"})

    await _router(
        poster,
        threads,
    ).send(
        _message(
            _post(
                "loki",
                "Review posted",
                attachments=[{"title": "Review posted", "color": "#2563eb"}],
            )
        )
    )

    assert poster.rich == [
        ("room-1", "Review posted", "sw_root", [{"title": "Review posted", "color": "#2563eb"}])
    ]
    assert poster.threaded == []
    assert threads.known == {PR_ID: "sw_root"}


async def test_a_rich_poster_without_a_bot_falls_back_to_markdown() -> None:
    poster, threads = RichPosterReturnsNone("sw_fallback"), FakeThreads()

    await _router(
        poster,
        threads,
    ).send(
        _message(
            _post(
                "heimdall",
                "Card ready",
                root=True,
                attachments=[{"title": "Card ready"}],
            )
        )
    )

    assert poster.rich == [("room-1", "Card ready", None, [{"title": "Card ready"}])]
    assert poster.threaded == [("Card ready", None)]
    assert threads.known == {PR_ID: "sw_fallback"}
