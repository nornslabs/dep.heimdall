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


CONFIG_MM = AgentConnectionConfig(
    base_url="http://switch.test",
    agent_id="agent-1",
    api_key="key-1",
    rooms=("r1",),
    connection_id="conn-1",
    mattermost_bot_token="mm-token-1",
    mattermost_team_id="team-1",
    mattermost_base_url="http://mattermost.test",
)
MM_POSTS_URL = "http://mattermost.test/api/v4/posts"


@respx.mock
async def test_progress_post_creates_a_threaded_post_and_returns_its_id() -> None:
    route = respx.post(MM_POSTS_URL).mock(return_value=httpx.Response(200, json={"id": "post-1"}))
    poster = AgentPoster(CONFIG_MM)

    post_id = await poster.post_progress("channel-1", "thread-1", "🔄 Fetching PR…")
    await poster.aclose()

    assert post_id == "post-1"
    assert route.call_count == 1
    request = route.calls.last.request
    assert json.loads(request.content) == {
        "channel_id": "channel-1",
        "root_id": "thread-1",
        "message": "🔄 Fetching PR…",
    }
    assert request.headers["authorization"] == "Bearer mm-token-1"


@respx.mock
async def test_progress_post_without_a_thread_roots_at_the_channel() -> None:
    route = respx.post(MM_POSTS_URL).mock(return_value=httpx.Response(200, json={"id": "post-2"}))
    poster = AgentPoster(CONFIG_MM)

    post_id = await poster.post_progress("channel-1", None, "🔄 Top-level progress")
    await poster.aclose()

    assert post_id == "post-2"
    assert json.loads(route.calls.last.request.content)["root_id"] == ""


@respx.mock
async def test_progress_update_patches_the_same_post_in_place() -> None:
    respx.post(MM_POSTS_URL).mock(return_value=httpx.Response(200, json={"id": "post-3"}))
    patch_route = respx.patch(f"{MM_POSTS_URL}/post-3").mock(
        return_value=httpx.Response(200, json={})
    )
    poster = AgentPoster(CONFIG_MM)

    post_id = await poster.post_progress("channel-1", None, "🔄 Fetching PR…")
    again = await poster.post_progress("channel-1", None, "✅ Done", update_post_id="post-3")
    await poster.aclose()

    assert post_id == again == "post-3"
    assert patch_route.call_count == 1
    assert json.loads(patch_route.calls.last.request.content) == {"message": "✅ Done"}


@respx.mock
async def test_progress_post_is_a_silent_noop_without_the_bot() -> None:
    route = respx.post(MM_POSTS_URL).mock(return_value=httpx.Response(200, json={"id": "post-x"}))
    poster = AgentPoster(CONFIG)

    assert await poster.post_progress("channel-1", None, "🔄 Fetching PR…") is None
    await poster.aclose()

    assert route.call_count == 0


@respx.mock
async def test_progress_post_swallows_errors() -> None:
    respx.post(MM_POSTS_URL).mock(side_effect=httpx.ConnectError("mattermost is down"))
    poster = AgentPoster(CONFIG_MM)

    assert await poster.post_progress("channel-1", None, "🔄 Fetching PR…") is None
    await poster.aclose()


@respx.mock
async def test_rich_post_sends_attachments_as_props() -> None:
    route = respx.post(MM_POSTS_URL).mock(return_value=httpx.Response(200, json={"id": "post-4"}))
    poster = AgentPoster(CONFIG_MM)

    post_id = await poster.post_rich(
        "channel-1",
        "PR #42 — Card ready",
        "thread-1",
        attachments=[{"title": "Card ready", "color": "#16a34a"}],
    )
    await poster.aclose()

    assert post_id == "post-4"
    assert json.loads(route.calls.last.request.content) == {
        "channel_id": "channel-1",
        "root_id": "thread-1",
        "message": "PR #42 — Card ready",
        "props": {"attachments": [{"title": "Card ready", "color": "#16a34a"}]},
    }


@respx.mock
async def test_rich_post_is_none_without_the_bot() -> None:
    route = respx.post(MM_POSTS_URL).mock(return_value=httpx.Response(200, json={"id": "post-y"}))
    poster = AgentPoster(CONFIG)

    assert await poster.post_rich("channel-1", "text", None, attachments=[{"title": "a"}]) is None
    await poster.aclose()

    assert route.call_count == 0


@respx.mock
@pytest.mark.parametrize("status", [403, 404])
async def test_rich_post_client_errors_raise_switch_request_failed(status: int) -> None:
    respx.post(MM_POSTS_URL).mock(return_value=httpx.Response(status))
    poster = AgentPoster(CONFIG_MM)

    with pytest.raises(SwitchRequestFailed):
        await poster.post_rich("channel-1", "text", None, attachments=[{"title": "a"}])
    await poster.aclose()


@respx.mock
async def test_rich_post_timeout_raises_switch_unavailable() -> None:
    respx.post(MM_POSTS_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    poster = AgentPoster(CONFIG_MM)

    with pytest.raises(SwitchUnavailable):
        await poster.post_rich("channel-1", "text", None, attachments=[{"title": "a"}])
    await poster.aclose()
