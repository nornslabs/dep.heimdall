"""Tests for finding the bridged triage room's Switch id through the Gateway API."""

import httpx
import respx

from heimdall.adapters.switch.provisioning import SwitchAdmin

BASE_URL = "http://switch:8000"
ROOMS_URL = f"{BASE_URL}/gateway/rooms"
LOGIN_URL = f"{BASE_URL}/gateway/auth/login"
TRIAGE_ID = "70ce5e33-5743-4e18-abe4-c7eeea35ade4"


def _room(room_id: str, slug: str, bridge_type: str = "mattermost") -> dict[str, str]:
    return {
        "id": room_id,
        "name": slug.title(),
        "bridge_type": bridge_type,
        "external_channel_url": f"{bridge_type}://mattermost:8065/heimdall/channels/{slug}",
    }


@respx.mock
async def test_find_room_id_returns_the_room_bridged_from_the_channel() -> None:
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(200, json={}))
    respx.get(ROOMS_URL).mock(
        return_value=httpx.Response(
            200, json=[_room("other-id", "off-topic"), _room(TRIAGE_ID, "triage")]
        )
    )

    async with SwitchAdmin(BASE_URL, "admin@example.com", "pw") as admin:
        found = await admin.find_room_id(channel_slug="triage")

    assert found == TRIAGE_ID


@respx.mock
async def test_find_room_id_is_none_until_the_channel_is_bridged() -> None:
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(200, json={}))
    respx.get(ROOMS_URL).mock(
        return_value=httpx.Response(200, json=[_room("other-id", "off-topic")])
    )

    async with SwitchAdmin(BASE_URL, "admin@example.com", "pw") as admin:
        found = await admin.find_room_id(channel_slug="triage")

    assert found is None


@respx.mock
async def test_find_room_id_ignores_rooms_from_other_bridges() -> None:
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(200, json={}))
    respx.get(ROOMS_URL).mock(
        return_value=httpx.Response(200, json=[_room("slack-id", "triage", "slack")])
    )

    async with SwitchAdmin(BASE_URL, "admin@example.com", "pw") as admin:
        found = await admin.find_room_id(channel_slug="triage")

    assert found is None
