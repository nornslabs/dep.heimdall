"""Hold an agent's Switch connection beside its job worker, so it can answer a ping."""

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Protocol

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from agents._presence_handler import PresenceHandler
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.adapters.switch.agent import AgentConnectionConfig, AgentMessage, SwitchAgentClient
from heimdall.adapters.switch.agent.credentials import load_agent_credentials, switch_api_url
from heimdall.modules.tenancy.api import linked_agent_room_ids
from heimdall.shared.agents import AgentName
from heimdall.shared.errors import ConfigInvalid
from heimdall.shared.logging import get_logger

log = get_logger(__name__)

OnMessage = Callable[[AgentMessage], Awaitable[None]]


class PresenceClient(Protocol):
    """The part of ``SwitchAgentClient`` this module drives; tests substitute a fake."""

    async def run(self, stop: asyncio.Event) -> None:
        """Hold the connection until ``stop`` is set or it must end."""
        ...

    async def aclose(self) -> None:
        """Release the connection's HTTP resources."""
        ...


ClientFactory = Callable[[AgentConnectionConfig, OnMessage], PresenceClient]


def _switch_client(config: AgentConnectionConfig, on_message: OnMessage) -> PresenceClient:
    """Build the real Switch agent client."""
    return SwitchAgentClient(config, on_message)


def claimable_rooms(linked: Iterable[str], default_room_id: str | None) -> tuple[str, ...]:
    """Join the rooms repos link with the configured default into the rooms to claim.

    Args:
        linked: Switch room UUIDs that enabled repos link for their agents.
        default_room_id: The default agent room from settings, if any.

    Returns:
        Each non-blank room once, in a stable order.
    """
    candidates = [*linked, default_room_id or ""]
    return tuple(sorted({room.strip() for room in candidates if room.strip()}))


async def run_with_presence(
    agent: AgentName,
    sessions: async_sessionmaker[AsyncSession],
    run_jobs: Callable[[], Awaitable[None]],
    *,
    default_room_id: str | None = None,
    environ: Mapping[str, str] | None = None,
    client_factory: ClientFactory = _switch_client,
) -> None:
    """Run the agent's jobs and, when it has a Switch key, its connection, until the jobs end.

    The connection is a convenience, not the agent's job: a missing key, no rooms to claim, or a
    connection that dies is logged and the jobs carry on. If the jobs end or fail, the
    connection is told to stop and closed.

    Rooms are read once at start, so a repo linked later needs an agent restart.

    Args:
        agent: The agent this process runs.
        sessions: Tenant database session factory.
        run_jobs: Runs the agent's job worker until it is told to stop.
        default_room_id: Default agent room from settings, claimed even if no repo links it yet.
        environ: Variables holding ``SWITCH_<AGENT>_*``; the process environment when omitted.
        client_factory: Builds the Switch client; tests pass a fake.
    """
    client = await _build_client(agent, sessions, default_room_id, environ, client_factory)
    if client is None:
        await run_jobs()
        return
    stop = asyncio.Event()
    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(_run_jobs_then_stop(run_jobs, stop))
            group.create_task(_hold_connection(agent, client, stop))
    finally:
        await client.aclose()


async def _build_client(
    agent: AgentName,
    sessions: async_sessionmaker[AsyncSession],
    default_room_id: str | None,
    environ: Mapping[str, str] | None,
    client_factory: ClientFactory,
) -> PresenceClient | None:
    """Create the connection, or None (with a log line saying why) when it cannot be made."""
    try:
        config = load_agent_credentials(
            agent.value, switch_api_url(environ), rooms=(), environ=environ
        )
    except ConfigInvalid as error:
        log.warning("presence_disabled", agent=agent.value, reason=str(error))
        return None
    async with unit_of_work(sessions) as session:
        linked = await linked_agent_room_ids(session)
    rooms = claimable_rooms(linked, default_room_id)
    if not rooms:
        log.warning("presence_disabled", agent=agent.value, reason="no agent room is linked")
        return None
    handler = PresenceHandler(agent, sessions)
    return client_factory(dataclasses.replace(config, rooms=rooms), handler.handle)


async def _run_jobs_then_stop(run_jobs: Callable[[], Awaitable[None]], stop: asyncio.Event) -> None:
    """Run the jobs; whatever ends them also ends the connection."""
    try:
        await run_jobs()
    finally:
        stop.set()


async def _hold_connection(agent: AgentName, client: PresenceClient, stop: asyncio.Event) -> None:
    """Keep the connection up; if it dies for good, log it and let the jobs carry on."""
    try:
        await client.run(stop)
    except Exception as error:
        log.error("presence_connection_failed", agent=agent.value, error_type=type(error).__name__)
