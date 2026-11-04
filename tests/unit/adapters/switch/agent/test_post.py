"""Tests for posting as an agent through POST /agents/{id}/message."""

import asyncio
import json

import httpx
import pytest
import respx

from heimdall.adapters.switch.agent._post import AgentPoster
from heimdall.adapters.switch.agent.config import AgentConnectionConfig
from heimdall.shared.errors import SwitchRequestFailed, SwitchUnavailable

CONFIG = AgentConnectionConfig(
    base_url="http://switch.test",
    agent_id="agent-1",
    api_key="key-1",
    rooms=("r1",),
    connection_id="conn-1",
)
URL = "http://switch.test/agents/agent-1/message"


@respx.mock
async def test_post_sends_body_bearer_key_and_connection_header() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={}))
    poster = AgentPoster(CONFIG)

    await poster.post("r1", "hello")
    await poster.aclose()

    request = route.calls.last.request
    assert json.loads(request.content) == {"room_id": "r1", "content": "hello"}
    assert request.headers["authorization"] == "Bearer key-1"
    assert request.headers["x-switch-connection-id"] == "conn-1"


@respx.mock
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_retryable_statuses_raise_switch_unavailable_without_retrying(status: int) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(status))
    poster = AgentPoster(CONFIG)

    with pytest.raises(SwitchUnavailable):
        await poster.post("r1", "hello")
    await poster.aclose()

    assert route.call_count == 1


@respx.mock
async def test_timeout_raises_switch_unavailable() -> None:
    respx.post(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    poster = AgentPoster(CONFIG)

    with pytest.raises(SwitchUnavailable):
        await poster.post("r1", "hello")
    await poster.aclose()


@respx.mock
@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_client_errors_are_permanent(status: int) -> None:
    respx.post(URL).mock(return_value=httpx.Response(status))
    poster = AgentPoster(CONFIG)

    with pytest.raises(SwitchRequestFailed):
        await poster.post("r1", "hello")
    await poster.aclose()


@respx.mock
async def test_concurrent_posts_are_sent_one_at_a_time_in_call_order() -> None:
    in_flight = 0
    max_in_flight = 0
    order: list[str] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        order.append(json.loads(request.content)["content"])
        await asyncio.sleep(0)
        in_flight -= 1
        return httpx.Response(200, json={})

    respx.post(URL).mock(side_effect=handle)
    poster = AgentPoster(CONFIG)

    await asyncio.gather(*(poster.post("r1", f"m{i}") for i in range(5)))
    await poster.aclose()

    assert max_in_flight == 1
    assert order == [f"m{i}" for i in range(5)]
