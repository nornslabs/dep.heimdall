"""The bridge shows it is working: 👀 on a mention, swapped for ✅ when the agent replies."""

import pytest

from apps.bridge._ack import DONE, WORKING, MentionAckHandler
from heimdall.adapters.switch.ports import RoomEvent
from heimdall.shared.errors import SwitchRequestFailed


class _FakeRoom:
    def __init__(self, *, fail: bool = False) -> None:
        self.added: list[tuple[str, str]] = []
        self.removed: list[tuple[str, str]] = []
        self.fail = fail

    async def react(self, post_id: str, emoji_name: str) -> None:
        if self.fail:
            raise SwitchRequestFailed("down")
        self.added.append((post_id, emoji_name))

    async def unreact(self, post_id: str, emoji_name: str) -> None:
        self.removed.append((post_id, emoji_name))


def _post(message: str, post_id: str = "p1", root_id: str = "t1") -> RoomEvent:
    return RoomEvent(
        kind="posted",
        channel_id="c1",
        post_id=post_id,
        user_id="u1",
        root_id=root_id,
        message=message,
    )


@pytest.mark.asyncio
async def test_a_mention_gets_the_working_reaction() -> None:
    room = _FakeRoom()

    await MentionAckHandler(room).handle(_post("@heimdall close the pr"))

    assert room.added == [("p1", WORKING)]


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["close the pr", "email@heimdall.example", "hello team"])
async def test_a_message_that_mentions_no_agent_is_ignored(message: str) -> None:
    room = _FakeRoom()

    await MentionAckHandler(room).handle(_post(message))

    assert room.added == []


@pytest.mark.asyncio
async def test_an_agents_own_reply_is_never_acked_even_when_it_names_another_agent() -> None:
    room = _FakeRoom()

    await MentionAckHandler(room).handle(
        _post("Heimdall: that's a question for @loki; ask them in this thread.")
    )

    assert room.added == []


@pytest.mark.asyncio
async def test_the_reply_swaps_working_for_done_on_every_pending_mention_of_that_agent() -> None:
    room = _FakeRoom()
    handler = MentionAckHandler(room)
    await handler.handle(_post("@heimdall one", post_id="p1"))
    await handler.handle(_post("@loki two", post_id="p2"))

    await handler.handle(_post("Heimdall: Done.", post_id="r1"))

    assert room.removed == [("p1", WORKING)]
    assert room.added[-1] == ("p1", DONE)
    assert ("p2", DONE) not in room.added


@pytest.mark.asyncio
async def test_a_reply_in_another_thread_leaves_the_mention_pending() -> None:
    room = _FakeRoom()
    handler = MentionAckHandler(room)
    await handler.handle(_post("@heimdall hi", post_id="p1", root_id="t1"))

    await handler.handle(_post("Heimdall: Done.", post_id="r1", root_id="t2"))

    assert room.removed == []


@pytest.mark.asyncio
async def test_a_second_reply_does_not_swap_again() -> None:
    room = _FakeRoom()
    handler = MentionAckHandler(room)
    await handler.handle(_post("@heimdall hi"))
    await handler.handle(_post("Heimdall: one", post_id="r1"))

    await handler.handle(_post("Heimdall: two", post_id="r2"))

    assert room.removed == [("p1", WORKING)]


@pytest.mark.asyncio
async def test_a_failed_reaction_never_breaks_the_bridge() -> None:
    room = _FakeRoom(fail=True)

    await MentionAckHandler(room).handle(_post("@heimdall hi"))

    assert room.added == []


@pytest.mark.asyncio
async def test_reactions_and_other_event_kinds_are_ignored() -> None:
    room = _FakeRoom()
    event = RoomEvent(kind="reaction_added", channel_id="c", post_id="p", user_id="u")

    await MentionAckHandler(room).handle(event)

    assert room.added == []
