"""Tests for posting into a thread as an agent through POST /agents/{id}/ops/post_message."""

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
URL = "http://switch.test/agents/agent-1/ops/post_message"


@respx.mock
async def test_a_post_with_no_thread_returns_the_event_id_that_can_root_a_thread() -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, json={"result": {"event_id": "sw_root"}})
    )
    poster = AgentPoster(CONFIG)

    event_id = await poster.post_in_thread("the checklist", None)
    await poster.aclose()

    request = route.calls.last.request
    assert event_id == "sw_root"
    assert json.loads(request.content) == {"body": "the checklist"}
    assert request.headers["authorization"] == "Bearer key-1"
    assert request.headers["x-switch-connection-id"] == "conn-1"


@respx.mock
async def test_a_reply_names_the_thread_it_belongs_to() -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, json={"result": {"event_id": "sw_reply"}})
    )
    poster = AgentPoster(CONFIG)

    await poster.post_in_thread("the verdict", "sw_root")
    await poster.aclose()

    assert json.loads(route.calls.last.request.content) == {
        "body": "the verdict",
        "thread_id": "sw_root",
    }


@respx.mock
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_server_trouble_is_retryable(status: int) -> None:
    respx.post(URL).mock(return_value=httpx.Response(status))
    poster = AgentPoster(CONFIG)

    with pytest.raises(SwitchUnavailable):
        await poster.post_in_thread("hello", None)
    await poster.aclose()


@respx.mock
@pytest.mark.parametrize("status", [400, 403, 404, 422])
async def test_a_refusal_is_not_retryable(status: int) -> None:
    respx.post(URL).mock(return_value=httpx.Response(status))
    poster = AgentPoster(CONFIG)

    with pytest.raises(SwitchRequestFailed):
        await poster.post_in_thread("hello", None)
    await poster.aclose()


@respx.mock
@pytest.mark.parametrize("body", [{}, {"result": {}}, {"result": {"event_id": ""}}, {"result": 1}])
async def test_a_reply_with_no_event_id_is_rejected(body: dict[str, object]) -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, json=body))
    poster = AgentPoster(CONFIG)

    with pytest.raises(SwitchRequestFailed):
        await poster.post_in_thread("hello", None)
    await poster.aclose()


@respx.mock
async def test_a_network_failure_is_retryable() -> None:
    respx.post(URL).mock(side_effect=httpx.ConnectError("down"))
    poster = AgentPoster(CONFIG)

    with pytest.raises(SwitchUnavailable):
        await poster.post_in_thread("hello", None)
    await poster.aclose()
