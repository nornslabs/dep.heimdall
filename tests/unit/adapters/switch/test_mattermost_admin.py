"""Tests for the Mattermost admin client that prepares the triage channel and bot token."""

import json

import httpx
import pytest
import respx

from heimdall.adapters.switch.provisioning import MattermostAdmin
from heimdall.shared.errors import SwitchRequestFailed, SwitchUnavailable

BASE_URL = "http://mattermost:8065"
API = f"{BASE_URL}/api/v4"
SESSION_TOKEN = "session-token-secret"
TEAM_ID = "team123"
CHANNEL_ID = "chan123"


def _sign_in_route() -> respx.Route:
    return respx.post(f"{API}/users/login").mock(
        return_value=httpx.Response(200, json={"id": "admin-id"}, headers={"Token": SESSION_TOKEN})
    )


@respx.mock
async def test_sign_in_sends_the_session_token_on_later_requests() -> None:
    _sign_in_route()
    team = respx.get(f"{API}/teams/name/heimdall").mock(
        return_value=httpx.Response(200, json={"id": TEAM_ID})
    )

    async with MattermostAdmin(BASE_URL, "mmadmin", "pw") as admin:
        team_id = await admin.team_id("heimdall")

    assert team_id == TEAM_ID
    assert team.calls.last.request.headers["authorization"] == f"Bearer {SESSION_TOKEN}"


@respx.mock
async def test_ensure_channel_reuses_an_existing_channel() -> None:
    _sign_in_route()
    respx.get(f"{API}/teams/{TEAM_ID}/channels/name/triage").mock(
        return_value=httpx.Response(200, json={"id": CHANNEL_ID})
    )
    create = respx.post(f"{API}/channels")

    async with MattermostAdmin(BASE_URL, "mmadmin", "pw") as admin:
        channel_id = await admin.ensure_channel(TEAM_ID, "triage")

    assert channel_id == CHANNEL_ID
    assert not create.called


@respx.mock
async def test_ensure_channel_creates_an_open_channel_when_missing() -> None:
    _sign_in_route()
    respx.get(f"{API}/teams/{TEAM_ID}/channels/name/triage").mock(
        return_value=httpx.Response(404, json={})
    )
    create = respx.post(f"{API}/channels").mock(
        return_value=httpx.Response(201, json={"id": CHANNEL_ID})
    )

    async with MattermostAdmin(BASE_URL, "mmadmin", "pw") as admin:
        channel_id = await admin.ensure_channel(TEAM_ID, "triage")

    body = json.loads(create.calls.last.request.content)
    assert channel_id == CHANNEL_ID
    assert body["name"] == "triage"
    assert body["team_id"] == TEAM_ID
    assert body["type"] == "O"


@respx.mock
async def test_add_members_looks_up_each_username_and_adds_it() -> None:
    _sign_in_route()
    respx.get(f"{API}/users/username/huginn").mock(
        return_value=httpx.Response(200, json={"id": "u-huginn"})
    )
    add = respx.post(f"{API}/channels/{CHANNEL_ID}/members").mock(
        return_value=httpx.Response(201, json={})
    )

    async with MattermostAdmin(BASE_URL, "mmadmin", "pw") as admin:
        await admin.add_members(CHANNEL_ID, ["huginn"])

    assert json.loads(add.calls.last.request.content) == {"user_id": "u-huginn"}


@respx.mock
async def test_add_members_fails_naming_the_missing_user() -> None:
    _sign_in_route()
    respx.get(f"{API}/users/username/loki").mock(return_value=httpx.Response(404, json={}))

    async with MattermostAdmin(BASE_URL, "mmadmin", "pw") as admin:
        with pytest.raises(SwitchRequestFailed, match="loki"):
            await admin.add_members(CHANNEL_ID, ["loki"])


@respx.mock
async def test_create_access_token_enables_personal_tokens_and_returns_the_secret() -> None:
    _sign_in_route()
    patch = respx.put(f"{API}/config/patch").mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{API}/users/me").mock(return_value=httpx.Response(200, json={"id": "admin-id"}))
    create = respx.post(f"{API}/users/admin-id/tokens").mock(
        return_value=httpx.Response(200, json={"token": "pat-secret"})
    )

    async with MattermostAdmin(BASE_URL, "mmadmin", "pw") as admin:
        token = await admin.create_access_token("heimdall")

    assert token == "pat-secret"
    config = json.loads(patch.calls.last.request.content)
    assert config["ServiceSettings"]["EnableUserAccessTokens"] is True
    assert json.loads(create.calls.last.request.content) == {"description": "heimdall"}


@respx.mock
async def test_token_works_is_true_only_when_mattermost_accepts_it() -> None:
    route = respx.get(f"{API}/users/me")
    route.side_effect = [httpx.Response(200, json={}), httpx.Response(401, json={})]

    assert await MattermostAdmin.token_works(BASE_URL, "good") is True
    assert await MattermostAdmin.token_works(BASE_URL, "stale") is False


@respx.mock
async def test_sign_in_failure_message_never_contains_the_password() -> None:
    respx.post(f"{API}/users/login").mock(return_value=httpx.Response(401, json={}))

    async with MattermostAdmin(BASE_URL, "mmadmin", "hunter2-secret") as admin:
        with pytest.raises(SwitchRequestFailed) as caught:
            await admin.team_id("heimdall")

    assert "hunter2-secret" not in str(caught.value)


@respx.mock
async def test_server_error_is_retryable() -> None:
    _sign_in_route()
    respx.get(f"{API}/teams/name/heimdall").mock(return_value=httpx.Response(503))

    async with MattermostAdmin(BASE_URL, "mmadmin", "pw") as admin:
        with pytest.raises(SwitchUnavailable):
            await admin.team_id("heimdall")
