"""Outbox rows that name an agent are posted to Switch under that agent's identity."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.columns import JsonObject
from heimdall.adapters.db.outbox.models import OutboxMessage
from heimdall.adapters.db.outbox.sender import send_due_messages
from heimdall.adapters.db.outbox.writer import write_outbox
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.adapters.switch.agent._post import AgentPoster
from heimdall.adapters.switch.agent.config import AgentConnectionConfig
from heimdall.adapters.switch.routing import SwitchRoutingSender
from heimdall.shared.agents import AgentName
from heimdall.shared.ids import InstallationId

pytestmark = pytest.mark.integration

BASE_URL = "http://switch.test"
BATCH = 1000  # tests share one schema, so leftover rows from other tests may be due too


class RecordingBot:
    """Stands in for the Mattermost bot and remembers which rows it was handed."""

    def __init__(self) -> None:
        self.sent_keys: list[str] = []

    async def send(self, message: OutboxMessage) -> None:
        self.sent_keys.append(message.idempotency_key)


def _poster(agent: AgentName) -> AgentPoster:
    config = AgentConnectionConfig(
        base_url=BASE_URL,
        agent_id=f"{agent.value}-id",
        api_key=f"{agent.value}-key",
        rooms=(),
        connection_id=f"{agent.value}-conn",
    )
    return AgentPoster(config)


def _router(bot: RecordingBot) -> SwitchRoutingSender:
    posters = {name: _poster(name) for name in (AgentName.HUGINN, AgentName.LOKI)}
    return SwitchRoutingSender(bot, posters)


async def _write(sessions: async_sessionmaker[AsyncSession], payload: JsonObject) -> str:
    key = f"test-{uuid.uuid4().hex}"
    async with unit_of_work(sessions) as session:
        await write_outbox(
            session,
            installation_id=InstallationId(55),
            channel="switch",
            kind="room.message",
            payload=payload,
            idempotency_key=key,
        )
    return key


async def _load(sessions: async_sessionmaker[AsyncSession], key: str) -> OutboxMessage:
    async with unit_of_work(sessions) as session:
        rows = await session.exec(select(OutboxMessage).where(OutboxMessage.idempotency_key == key))
        return rows.one()


async def _deliver(sessions: async_sessionmaker[AsyncSession], router: SwitchRoutingSender) -> None:
    now = datetime.now(UTC) + timedelta(seconds=1)
    await send_due_messages(sessions, {"switch": router}, now=now, batch=BATCH)


class AgentEndpoint:
    """A mocked ``/agents/{id}/message`` URL that records each post's body and bearer key."""

    def __init__(self, agent: AgentName) -> None:
        self.bodies: list[JsonObject] = []
        self.authorizations: list[str] = []
        respx.post(f"{BASE_URL}/agents/{agent.value}-id/message").mock(side_effect=self._record)

    def _record(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        self.authorizations.append(request.headers["authorization"])
        return httpx.Response(200, json={})


@respx.mock
async def test_huginn_row_is_posted_under_huginns_identity(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    huginn, loki = AgentEndpoint(AgentName.HUGINN), AgentEndpoint(AgentName.LOKI)
    bot = RecordingBot()
    key = await _write(
        sessions, {"agent": "huginn", "channel_id": "room-h", "message": "repro verdict"}
    )

    await _deliver(sessions, _router(bot))

    assert huginn.bodies == [{"room_id": "room-h", "content": "repro verdict"}]
    assert huginn.authorizations == ["Bearer huginn-key"]
    assert loki.bodies == []
    assert key not in bot.sent_keys
    row = await _load(sessions, key)
    assert row.status == "sent"
    assert row.idempotency_key == key


@respx.mock
async def test_loki_row_uses_lokis_key_not_huginns(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    huginn, loki = AgentEndpoint(AgentName.HUGINN), AgentEndpoint(AgentName.LOKI)
    await _write(sessions, {"agent": "loki", "channel_id": "room-l", "message": "findings"})

    await _deliver(sessions, _router(RecordingBot()))

    assert loki.bodies == [{"room_id": "room-l", "content": "findings"}]
    assert loki.authorizations == ["Bearer loki-key"]
    assert huginn.bodies == []


@respx.mock
async def test_row_without_an_agent_still_goes_through_the_bot(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    huginn = AgentEndpoint(AgentName.HUGINN)
    bot = RecordingBot()
    key = await _write(sessions, {"channel_id": "room-b", "message": "checklist"})

    await _deliver(sessions, _router(bot))

    assert key in bot.sent_keys
    assert huginn.bodies == []
    assert (await _load(sessions, key)).status == "sent"


@respx.mock
async def test_row_naming_an_unknown_agent_dies_at_once_and_alerts_the_room(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    bot = RecordingBot()
    key = await _write(sessions, {"agent": "odin", "channel_id": "room-x", "message": "secret"})

    await _deliver(sessions, _router(bot))

    row = await _load(sessions, key)
    alert = await _load(sessions, f"alert:{row.id}")
    assert (row.status, row.attempts, row.last_error) == ("dead", 1, "SwitchRequestFailed")
    assert key not in bot.sent_keys
    assert alert.payload["channel_id"] == "room-x"
    assert "secret" not in str(alert.payload["message"])


@respx.mock
async def test_agent_post_refused_by_switch_dies_and_its_alert_goes_through_the_bot(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    respx.post(f"{BASE_URL}/agents/huginn-id/message").mock(return_value=httpx.Response(403))
    bot = RecordingBot()
    key = await _write(sessions, {"agent": "huginn", "channel_id": "room-d", "message": "private"})

    await _deliver(sessions, _router(bot))

    row = await _load(sessions, key)
    alert = await _load(sessions, f"alert:{row.id}")
    assert (row.status, row.last_error) == ("dead", "SwitchRequestFailed")
    assert "agent" not in alert.payload  # alerts are not agent posts, so the bot delivers them
    assert alert.idempotency_key in bot.sent_keys
    assert "private" not in str(alert.payload["message"])
