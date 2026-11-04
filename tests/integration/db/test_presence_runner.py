"""An agent process holds its Switch connection beside its jobs, and a bad one never stops them."""

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from agents._presence_runner import run_with_presence
from heimdall.adapters.switch.agent import AgentConnectionConfig, AgentMessage
from heimdall.modules.tenancy.api import get_tenant, link_repo_room, on_installation_event
from heimdall.shared.agents import AgentName
from heimdall.shared.ids import InstallationId, RepoId
from tests.integration.db.installation_helpers import created_installation

pytestmark = pytest.mark.integration

CREDENTIALS = {"SWITCH_HUGINN_AGENT_ID": "agent-1", "SWITCH_HUGINN_API_KEY": "key-1"}
DEFAULT_ROOM = "default-room"

OnMessage = Callable[[AgentMessage], Awaitable[None]]


class FakeClient:
    """Stands in for the Switch connection: records its settings and how it was stopped."""

    def __init__(self, config: AgentConnectionConfig, on_message: OnMessage) -> None:
        self.config = config
        self.on_message = on_message
        self.stop_seen = False
        self.closed = False
        self.error: Exception | None = None

    async def run(self, stop: asyncio.Event) -> None:
        if self.error is not None:
            raise self.error
        await stop.wait()
        self.stop_seen = True

    async def aclose(self) -> None:
        self.closed = True


class Harness:
    """Builds fake clients and counts the jobs that ran."""

    def __init__(self) -> None:
        self.clients: list[FakeClient] = []
        self.jobs_run = 0
        self.client_error: Exception | None = None

    def make_client(self, config: AgentConnectionConfig, on_message: OnMessage) -> FakeClient:
        client = FakeClient(config, on_message)
        client.error = self.client_error
        self.clients.append(client)
        return client

    async def jobs(self) -> None:
        await asyncio.sleep(0.01)
        self.jobs_run += 1


async def _linked_room(sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine) -> str:
    room = f"room-{uuid.uuid4().hex}"
    installation_id, repo_id = (uuid.uuid4().int % 10**9 + 1 for _ in range(2))
    await on_installation_event(
        sessions, created_installation(installation_id, "octo-org", repo_id, f"octo-org/r{repo_id}")
    )
    ctx = await get_tenant(sessions, InstallationId(installation_id))
    await link_repo_room(sessions, ctx, RepoId(repo_id), "mm-channel", agent_room_id=room)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE repos SET config = CAST(:config AS jsonb) WHERE repo_id = :id"),
            {"config": json.dumps({"maintainers": ["alice"]}), "id": repo_id},
        )
    return room


async def _run(
    harness: Harness,
    sessions: async_sessionmaker[AsyncSession],
    environ: dict[str, str],
    default_room: str | None = DEFAULT_ROOM,
) -> None:
    await run_with_presence(
        AgentName.HUGINN,
        sessions,
        harness.jobs,
        default_room_id=default_room,
        environ=environ,
        client_factory=harness.make_client,
    )


async def test_the_connection_claims_linked_and_default_rooms_and_stops_with_the_jobs(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    room = await _linked_room(sessions, engine)
    harness = Harness()

    await _run(harness, sessions, CREDENTIALS)

    (client,) = harness.clients
    assert harness.jobs_run == 1
    assert {room, DEFAULT_ROOM} <= set(client.config.rooms)
    assert client.config.agent_id == "agent-1"
    assert client.stop_seen and client.closed


async def test_an_agent_with_no_switch_key_runs_its_jobs_without_a_connection(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    harness = Harness()

    await _run(harness, sessions, {})

    assert harness.jobs_run == 1
    assert harness.clients == []


async def test_a_half_set_key_runs_its_jobs_without_a_connection(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    harness = Harness()

    await _run(harness, sessions, {"SWITCH_HUGINN_AGENT_ID": "agent-1"})

    assert harness.jobs_run == 1
    assert harness.clients == []


async def test_a_connection_that_fails_never_stops_the_jobs(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    harness = Harness()
    harness.client_error = RuntimeError("socket refused")

    await _run(harness, sessions, CREDENTIALS)

    assert harness.jobs_run == 1
    assert harness.clients[0].closed


async def test_failing_jobs_stop_the_connection_and_raise(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    harness = Harness()

    async def failing_jobs() -> None:
        await asyncio.sleep(0.01)
        raise ValueError("queue gone")

    with pytest.raises(ExceptionGroup) as raised:
        await run_with_presence(
            AgentName.HUGINN,
            sessions,
            failing_jobs,
            default_room_id=DEFAULT_ROOM,
            environ=CREDENTIALS,
            client_factory=harness.make_client,
        )

    assert raised.group_contains(ValueError)
    assert harness.clients[0].stop_seen and harness.clients[0].closed
