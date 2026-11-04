"""Protocol behaviour of SwitchAgentClient against a scripted fake Switch server."""

import asyncio
from dataclasses import replace

import pytest

from heimdall.adapters.switch.agent import AgentConnectionConfig, SwitchAgentClient
from heimdall.adapters.switch.agent.protocol import AgentMessage, Gap
from heimdall.shared.errors import SwitchRequestFailed
from tests.unit.adapters.switch.agent.fake_switch import Session, running_switch

TIMEOUT = 3.0


def _config(base_url: str, **overrides: float) -> AgentConnectionConfig:
    return replace(
        AgentConnectionConfig(
            base_url=base_url,
            agent_id="agent-1",
            api_key="key-1",
            rooms=("r1",),
            connection_id="conn-1",
            initial_backoff_seconds=0.01,
            max_backoff_seconds=0.02,
            drain_timeout_seconds=0.2,
        ),
        **overrides,
    )


class Recorder:
    """Collects handled messages and gaps."""

    def __init__(self) -> None:
        self.messages: list[AgentMessage] = []
        self.gaps: list[Gap] = []
        self.block = asyncio.Event()
        self.block.set()

    async def on_message(self, message: AgentMessage) -> None:
        await self.block.wait()
        self.messages.append(message)

    async def on_gap(self, gap: Gap) -> None:
        self.gaps.append(gap)


async def _start(
    base_url: str, recorder: Recorder, **overrides: float
) -> tuple[SwitchAgentClient, asyncio.Event, asyncio.Task[None]]:
    client = SwitchAgentClient(_config(base_url, **overrides), recorder.on_message, recorder.on_gap)
    stop = asyncio.Event()
    return client, stop, asyncio.create_task(client.run(stop))


async def _finish(client: SwitchAgentClient, stop: asyncio.Event, task: asyncio.Task[None]) -> None:
    stop.set()
    await asyncio.wait_for(task, TIMEOUT)
    await client.aclose()


async def test_opens_with_key_connection_id_and_rooms_and_answers_pings() -> None:
    async def script(session: Session) -> None:
        await session.state(cursor=7)
        assert await session.ping_and_wait_for_pong() == 7
        await session.wait_closed()

    async with running_switch([script]) as fake:
        client, stop, task = await _start(fake.base_url, Recorder())
        await asyncio.wait_for(fake.connected[0].wait(), TIMEOUT)
        await asyncio.sleep(0.1)
        await _finish(client, stop, task)

    session = fake.sessions[0]
    assert session.headers["authorization"] == "Bearer key-1"
    assert session.query["connection_id"] == "conn-1"
    assert session.query["rooms"] == "r1"
    assert "start_from" not in session.query
    assert session.pongs == [7]


async def test_pong_is_sent_while_the_handler_is_blocked() -> None:
    recorder = Recorder()
    recorder.block.clear()
    observed: list[int] = []

    async def script(session: Session) -> None:
        await session.state()
        await session.message(1)
        observed.append(await session.ping_and_wait_for_pong())
        recorder.block.set()
        await asyncio.sleep(0.1)
        observed.append(await session.ping_and_wait_for_pong())
        await session.wait_closed()

    async with running_switch([script]) as fake:
        client, stop, task = await _start(fake.base_url, recorder)
        await asyncio.wait_for(fake.connected[0].wait(), TIMEOUT)
        await asyncio.sleep(0.4)
        await _finish(client, stop, task)

    assert observed == [0, 1]
    assert [m.message_id for m in recorder.messages] == ["m1"]


async def test_only_addressed_messages_reach_the_handler() -> None:
    recorder = Recorder()

    async def script(session: Session) -> None:
        await session.state()
        await session.message(1, addressed=False)
        await session.message(2)
        await session.ping_and_wait_for_pong()
        await session.wait_closed()

    async with running_switch([script]) as fake:
        client, stop, task = await _start(fake.base_url, recorder)
        await asyncio.sleep(0.3)
        await _finish(client, stop, task)

    assert [m.message_id for m in recorder.messages] == ["m2"]


async def test_reconnect_reuses_connection_id_and_resumes_from_cursor_and_generation() -> None:
    async def first(session: Session) -> None:
        await session.state(generation=3)
        await session.message(5)
        await session.ws.close(1012)

    async def second(session: Session) -> None:
        await session.state(generation=4, cursor=5)
        await session.wait_closed()

    async with running_switch([first, second]) as fake:
        client, stop, task = await _start(fake.base_url, Recorder())
        await asyncio.wait_for(fake.connected[1].wait(), TIMEOUT)
        await _finish(client, stop, task)

    resumed = fake.sessions[1].query
    assert resumed["connection_id"] == "conn-1"
    assert resumed["start_from"] == "5"
    assert resumed["expected_generation"] == "3"


@pytest.mark.parametrize("code", ["heartbeat_lapsed", "no_stream", "unfenced", "connection_error"])
async def test_resumable_evictions_reconnect(code: str) -> None:
    async def first(session: Session) -> None:
        await session.state()
        await session.send("evicted", {"code": code, "reason": "x"})
        await session.ws.close(1000)

    async def second(session: Session) -> None:
        await session.state()
        await session.wait_closed()

    async with running_switch([first, second]) as fake:
        client, stop, task = await _start(fake.base_url, Recorder())
        await asyncio.wait_for(fake.connected[1].wait(), TIMEOUT)
        await _finish(client, stop, task)


@pytest.mark.parametrize("code", ["taken_over", "closed"])
async def test_final_evictions_stop_without_reconnecting(code: str) -> None:
    async def first(session: Session) -> None:
        await session.state()
        await session.send("evicted", {"code": code, "reason": "x"})
        await session.ws.close(1000)

    async with running_switch([first]) as fake:
        client = SwitchAgentClient(_config(fake.base_url), Recorder().on_message)
        await asyncio.wait_for(client.run(asyncio.Event()), TIMEOUT)
        await client.aclose()
        await asyncio.sleep(0.1)

    assert len(fake.sessions) == 1


async def test_bad_api_key_close_4401_is_permanent() -> None:
    async def first(session: Session) -> None:
        await session.ws.close(4401)

    async with running_switch([first]) as fake:
        client = SwitchAgentClient(_config(fake.base_url), Recorder().on_message)
        with pytest.raises(SwitchRequestFailed):
            await asyncio.wait_for(client.run(asyncio.Event()), TIMEOUT)
        await client.aclose()

    assert len(fake.sessions) == 1


async def test_refusal_frame_with_4409_close_is_permanent() -> None:
    async def first(session: Session) -> None:
        await session.send("refused", {"status": 409, "detail": "room already claimed"})
        await session.ws.close(4409)

    async with running_switch([first]) as fake:
        client = SwitchAgentClient(_config(fake.base_url), Recorder().on_message)
        with pytest.raises(SwitchRequestFailed):
            await asyncio.wait_for(client.run(asyncio.Event()), TIMEOUT)
        await client.aclose()

    assert len(fake.sessions) == 1


async def test_gap_moves_cursor_to_resumed_at_and_notifies_handler() -> None:
    recorder = Recorder()
    observed: list[int] = []

    async def script(session: Session) -> None:
        await session.state()
        await session.send(
            "gap",
            {"from_sequence": 1, "resumed_at": 40, "rooms": ["r1"], "all_rooms": False},
        )
        await asyncio.sleep(0.1)
        observed.append(await session.ping_and_wait_for_pong())
        await session.wait_closed()

    async with running_switch([script]) as fake:
        client, stop, task = await _start(fake.base_url, recorder)
        await asyncio.sleep(0.4)
        await _finish(client, stop, task)

    assert observed == [40]
    assert [gap.resumed_at for gap in recorder.gaps] == [40]


async def test_queue_overflow_resumes_without_losing_or_duplicating_messages() -> None:
    recorder = Recorder()
    recorder.block.clear()

    async def first(session: Session) -> None:
        await session.state()
        for sequence in (1, 2, 3, 4):
            await session.message(sequence)
        await session.wait_closed()

    async def second(session: Session) -> None:
        start = int(session.query["start_from"])
        await session.state(cursor=start)
        recorder.block.set()
        for sequence in range(start + 1, 5):
            await session.message(sequence)
        await session.wait_closed()

    async with running_switch([first, second]) as fake:
        client, stop, task = await _start(fake.base_url, recorder, queue_size=1)
        await asyncio.wait_for(fake.connected[1].wait(), TIMEOUT)
        await asyncio.sleep(0.4)
        await _finish(client, stop, task)

    assert [m.sequence for m in recorder.messages] == [1, 2, 3, 4]


async def test_stop_with_a_blocked_handler_returns_within_the_drain_timeout() -> None:
    recorder = Recorder()
    recorder.block.clear()

    async def script(session: Session) -> None:
        await session.state()
        await session.message(1)
        await session.wait_closed()

    async with running_switch([script]) as fake:
        client, stop, task = await _start(fake.base_url, recorder)
        await asyncio.wait_for(fake.connected[0].wait(), TIMEOUT)
        await asyncio.sleep(0.1)
        await _finish(client, stop, task)
        await asyncio.wait_for(fake.sessions[0].wait_closed(), TIMEOUT)


async def test_a_second_run_on_the_same_client_is_rejected() -> None:
    async def script(session: Session) -> None:
        await session.state()
        await session.wait_closed()

    async with running_switch([script]) as fake:
        client, stop, task = await _start(fake.base_url, Recorder())
        await asyncio.wait_for(fake.connected[0].wait(), TIMEOUT)
        with pytest.raises(RuntimeError):
            await client.run(asyncio.Event())
        await _finish(client, stop, task)
