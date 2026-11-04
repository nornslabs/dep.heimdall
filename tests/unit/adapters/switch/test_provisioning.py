"""Tests for registering agents and setting their addressing through the Switch HTTP API."""

import json

import httpx
import pytest
import respx

from heimdall.adapters.switch.provisioning import (
    AgentRegistrar,
    AgentSpec,
    SwitchAdmin,
    triage_room_policy,
)
from heimdall.shared.errors import (
    SwitchAgentExists,
    SwitchRequestFailed,
    SwitchUnavailable,
)

BASE_URL = "http://switch.test"
TOKEN = "registration-token-secret"
SPEC = AgentSpec(name="huginn", description="Reproduces the bug.")


def _created(agent_id: str = "agent-1", api_key: str = "key-secret-1") -> httpx.Response:
    return httpx.Response(
        201, json={"id": agent_id, "api_key": api_key, "oauth_client_id": "oauth-1"}
    )


@respx.mock
async def test_register_sends_profile_and_registration_token() -> None:
    route = respx.post(f"{BASE_URL}/agents").mock(return_value=_created())

    async with AgentRegistrar(BASE_URL, TOKEN) as registrar:
        await registrar.register(SPEC)

    request = route.calls.last.request
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    body = json.loads(request.content)
    assert body == {
        "name": "huginn",
        "description": "Reproduces the bug.",
        "connector_type": "heimdall",
        "integration_profile": {
            "connection_model": "always_on",
            "message_exchange": True,
            "pre_invocation_mediation": [],
            "post_invocation_mediation": [],
            "event_reporting": [],
        },
        "overwrite": False,
    }


@respx.mock
async def test_register_returns_id_and_key() -> None:
    respx.post(f"{BASE_URL}/agents").mock(return_value=_created("agent-9", "key-9"))

    async with AgentRegistrar(BASE_URL, TOKEN) as registrar:
        registration = await registrar.register(SPEC)

    assert registration.agent_id == "agent-9"
    assert registration.api_key == "key-9"


@respx.mock
async def test_registration_repr_never_shows_the_api_key() -> None:
    respx.post(f"{BASE_URL}/agents").mock(return_value=_created(api_key="key-secret-1"))

    async with AgentRegistrar(BASE_URL, TOKEN) as registrar:
        registration = await registrar.register(SPEC)

    assert "key-secret-1" not in repr(registration)


@respx.mock
async def test_overwrite_flag_is_sent_to_rotate_the_key() -> None:
    route = respx.post(f"{BASE_URL}/agents").mock(return_value=_created())

    async with AgentRegistrar(BASE_URL, TOKEN) as registrar:
        await registrar.register(SPEC, overwrite=True)

    assert json.loads(route.calls.last.request.content)["overwrite"] is True


@respx.mock
async def test_duplicate_name_raises_agent_exists_naming_the_agent() -> None:
    respx.post(f"{BASE_URL}/agents").mock(
        return_value=httpx.Response(409, json={"detail": "Agent huginn exists"})
    )

    async with AgentRegistrar(BASE_URL, TOKEN) as registrar:
        with pytest.raises(SwitchAgentExists, match="huginn"):
            await registrar.register(SPEC)


@respx.mock
async def test_bad_registration_token_is_a_permanent_failure_without_the_token() -> None:
    respx.post(f"{BASE_URL}/agents").mock(return_value=httpx.Response(401, text="nope"))

    async with AgentRegistrar(BASE_URL, TOKEN) as registrar:
        with pytest.raises(SwitchRequestFailed) as caught:
            await registrar.register(SPEC)

    assert TOKEN not in str(caught.value)


@respx.mock
async def test_server_error_is_retryable() -> None:
    respx.post(f"{BASE_URL}/agents").mock(return_value=httpx.Response(503))

    async with AgentRegistrar(BASE_URL, TOKEN) as registrar:
        with pytest.raises(SwitchUnavailable):
            await registrar.register(SPEC)


@respx.mock
async def test_unreachable_switch_is_retryable() -> None:
    respx.post(f"{BASE_URL}/agents").mock(side_effect=httpx.ConnectError("refused"))

    async with AgentRegistrar(BASE_URL, TOKEN) as registrar:
        with pytest.raises(SwitchUnavailable):
            await registrar.register(SPEC)


def test_triage_policy_admits_only_humans_in_the_triage_room() -> None:
    policy = triage_room_policy("room-triage")

    assert policy == {
        "rules": [
            {
                "rooms": ["room-triage"],
                "room_groups": "*",
                "users": "*",
                "agents": [],
                "owner": False,
                "owner_agents": False,
                "platform": False,
            }
        ]
    }


@respx.mock
async def test_admin_logs_in_then_puts_the_policy_with_the_session_cookie() -> None:
    respx.post(f"{BASE_URL}/gateway/auth/login").mock(
        return_value=httpx.Response(
            200, json={"id": "u1"}, headers={"set-cookie": "sid=abc; Path=/"}
        )
    )
    put = respx.put(f"{BASE_URL}/gateway/agents/agent-1/addressing-policy").mock(
        return_value=httpx.Response(200, json={})
    )

    async with SwitchAdmin(BASE_URL, "admin@example.com", "pw-secret") as admin:
        await admin.set_addressing(agent_id="agent-1", room_id="room-triage")

    request = put.calls.last.request
    assert "sid=abc" in request.headers["cookie"]
    assert json.loads(request.content) == {"policy": triage_room_policy("room-triage")}


@respx.mock
async def test_admin_login_failure_is_permanent_and_hides_the_password() -> None:
    respx.post(f"{BASE_URL}/gateway/auth/login").mock(return_value=httpx.Response(401))

    async with SwitchAdmin(BASE_URL, "admin@example.com", "pw-secret") as admin:
        with pytest.raises(SwitchRequestFailed) as caught:
            await admin.set_addressing(agent_id="agent-1", room_id="room-triage")

    assert "pw-secret" not in str(caught.value)


@respx.mock
async def test_admin_logs_in_once_for_several_agents() -> None:
    login = respx.post(f"{BASE_URL}/gateway/auth/login").mock(
        return_value=httpx.Response(200, json={}, headers={"set-cookie": "sid=abc; Path=/"})
    )
    respx.put(url__regex=rf"{BASE_URL}/gateway/agents/.+/addressing-policy").mock(
        return_value=httpx.Response(200, json={})
    )

    async with SwitchAdmin(BASE_URL, "admin@example.com", "pw") as admin:
        await admin.set_addressing(agent_id="a1", room_id="r")
        await admin.set_addressing(agent_id="a2", room_id="r")

    assert login.call_count == 1


@respx.mock
async def test_rate_limited_registration_is_retryable() -> None:
    respx.post(f"{BASE_URL}/agents").mock(return_value=httpx.Response(429))

    async with AgentRegistrar(BASE_URL, TOKEN) as registrar:
        with pytest.raises(SwitchUnavailable):
            await registrar.register(SPEC)


@respx.mock
@pytest.mark.parametrize("body", [b"not json", b'{"id": "agent-1"}', b'{"api_key": "k"}'])
async def test_an_unreadable_reply_is_a_permanent_failure_that_hides_the_body(body: bytes) -> None:
    respx.post(f"{BASE_URL}/agents").mock(return_value=httpx.Response(201, content=body))

    async with AgentRegistrar(BASE_URL, TOKEN) as registrar:
        with pytest.raises(SwitchRequestFailed) as caught:
            await registrar.register(SPEC)

    assert "not json" not in str(caught.value)
    assert "may be registered" in str(caught.value)


@respx.mock
@pytest.mark.parametrize("bad_id", ["../auth", "a?b", "a/b", "", "x" * 65])
async def test_an_agent_id_that_could_alter_a_url_path_is_rejected(bad_id: str) -> None:
    respx.post(f"{BASE_URL}/agents").mock(return_value=_created(agent_id=bad_id))

    async with AgentRegistrar(BASE_URL, TOKEN) as registrar:
        with pytest.raises(SwitchRequestFailed):
            await registrar.register(SPEC)


@respx.mock
async def test_a_refused_policy_update_is_a_permanent_failure() -> None:
    respx.post(f"{BASE_URL}/gateway/auth/login").mock(
        return_value=httpx.Response(200, json={}, headers={"set-cookie": "sid=abc; Path=/"})
    )
    respx.put(f"{BASE_URL}/gateway/agents/agent-1/addressing-policy").mock(
        return_value=httpx.Response(403)
    )

    async with SwitchAdmin(BASE_URL, "admin@example.com", "pw") as admin:
        with pytest.raises(SwitchRequestFailed):
            await admin.set_addressing(agent_id="agent-1", room_id="r")


@respx.mock
async def test_sign_in_up_front_means_later_updates_do_not_log_in_again() -> None:
    login = respx.post(f"{BASE_URL}/gateway/auth/login").mock(
        return_value=httpx.Response(200, json={}, headers={"set-cookie": "sid=abc; Path=/"})
    )
    respx.put(f"{BASE_URL}/gateway/agents/agent-1/addressing-policy").mock(
        return_value=httpx.Response(200, json={})
    )

    async with SwitchAdmin(BASE_URL, "admin@example.com", "pw") as admin:
        await admin.sign_in()
        await admin.set_addressing(agent_id="agent-1", room_id="r")

    assert login.call_count == 1


@respx.mock
async def test_a_rejected_login_is_tried_only_once_per_call() -> None:
    login = respx.post(f"{BASE_URL}/gateway/auth/login").mock(return_value=httpx.Response(401))

    async with SwitchAdmin(BASE_URL, "admin@example.com", "pw") as admin:
        with pytest.raises(SwitchRequestFailed):
            await admin.sign_in()

    assert login.call_count == 1
