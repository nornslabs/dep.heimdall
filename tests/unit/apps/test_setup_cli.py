"""Tests for the pre-compose setup command that produces the Switch values for ``.env``."""

import json
from pathlib import Path

import httpx
import pytest
import respx

from apps.bridge._env_file import read_env_file
from apps.bridge.setup import parse_args, run
from heimdall.shared.errors import ConfigInvalid

MM = "http://mattermost:8065"
MM_API = f"{MM}/api/v4"
SWITCH = "http://switch:8000"
ROOM_UUID = "70ce5e33-5743-4e18-abe4-c7eeea35ade4"
CHANNEL_ID = "chan123"
TOKEN = "pat-secret-value"
AGENTS = ("huginn", "loki", "heimdall")


async def _no_sleep(_seconds: float) -> None:
    return None


def _environ() -> dict[str, str]:
    return {
        "SWITCH_API_URL": SWITCH,
        "AGENT_REGISTRATION_TOKEN": "registration-secret",
        "GATEWAY_ADMIN_EMAIL": "admin@example.com",
        "GATEWAY_ADMIN_PASSWORD": "gateway-secret",
        "MATTERMOST_ADMIN_USER": "mmadmin",
        "MATTERMOST_ADMIN_PASSWORD": "mm-secret",
        "MATTERMOST_TEAM_NAME": "heimdall",
        "MATTERMOST_USER": "maintainer",
    }


def _argv(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "--mattermost-url",
        MM,
        "--env-file",
        str(tmp_path / ".env"),
        "--secrets-file",
        str(tmp_path / "agents.env"),
        *extra,
    ]


def _register_answer(request: httpx.Request) -> httpx.Response:
    name = json.loads(request.content)["name"]
    return httpx.Response(201, json={"id": f"id-{name}", "api_key": f"key-{name}"})


def _mock_stack(*, bridged: bool = True) -> dict[str, respx.Route]:
    """Mock a healthy Mattermost + Switch where the channel does not exist yet."""
    routes: dict[str, respx.Route] = {}
    respx.get(f"{SWITCH}/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    respx.get(f"{MM_API}/system/ping").mock(return_value=httpx.Response(200, json={}))
    respx.post(f"{MM_API}/users/login").mock(
        return_value=httpx.Response(200, json={"id": "admin-id"}, headers={"Token": "session"})
    )
    respx.get(f"{MM_API}/teams/name/heimdall").mock(
        return_value=httpx.Response(200, json={"id": "team1"})
    )
    respx.get(f"{MM_API}/teams/team1/channels/name/triage").mock(
        return_value=httpx.Response(404, json={})
    )
    routes["channel"] = respx.post(f"{MM_API}/channels").mock(
        return_value=httpx.Response(201, json={"id": CHANNEL_ID})
    )
    respx.put(f"{MM_API}/config/patch").mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{MM_API}/users/me").mock(return_value=httpx.Response(200, json={"id": "admin-id"}))
    routes["token"] = respx.post(f"{MM_API}/users/admin-id/tokens").mock(
        return_value=httpx.Response(200, json={"token": TOKEN})
    )
    for name in (*AGENTS, "maintainer"):
        respx.get(f"{MM_API}/users/username/{name}").mock(
            return_value=httpx.Response(200, json={"id": f"u-{name}"})
        )
    routes["members"] = respx.post(f"{MM_API}/channels/{CHANNEL_ID}/members").mock(
        return_value=httpx.Response(201, json={})
    )
    routes["register"] = respx.post(f"{SWITCH}/agents").mock(side_effect=_register_answer)
    respx.post(f"{SWITCH}/gateway/auth/login").mock(return_value=httpx.Response(200, json={}))
    rooms = (
        [
            {
                "id": ROOM_UUID,
                "bridge_type": "mattermost",
                "external_channel_url": "mattermost://mattermost:8065/heimdall/channels/triage",
            }
        ]
        if bridged
        else []
    )
    respx.get(f"{SWITCH}/gateway/rooms").mock(return_value=httpx.Response(200, json=rooms))
    routes["addressing"] = respx.put(url__regex=rf"{SWITCH}/gateway/agents/.+/addressing-policy")
    routes["addressing"].mock(return_value=httpx.Response(200, json={}))
    return routes


def test_parse_args_defaults() -> None:
    args = parse_args([])

    assert args.channel == "triage"
    assert args.env_file == Path(".env")
    assert args.show_token is False


@respx.mock
async def test_writes_all_three_values_to_the_env_file(tmp_path: Path) -> None:
    _mock_stack()

    code = await run(_argv(tmp_path), _environ(), sleep=_no_sleep)

    written = read_env_file(tmp_path / ".env")
    assert code == 0
    assert written["HEIMDALL_SWITCH_TOKEN"] == TOKEN
    assert written["HEIMDALL_DEFAULT_SWITCH_ROOM_ID"] == CHANNEL_ID
    assert written["HEIMDALL_DEFAULT_SWITCH_AGENT_ROOM_ID"] == ROOM_UUID


@respx.mock
async def test_registers_agents_adds_bots_and_sets_addressing(tmp_path: Path) -> None:
    routes = _mock_stack()

    await run(_argv(tmp_path), _environ(), sleep=_no_sleep)

    assert routes["register"].call_count == len(AGENTS)
    assert routes["members"].call_count == len(AGENTS) + 1
    assert routes["addressing"].call_count == len(AGENTS)
    assert "SWITCH_LOKI_API_KEY" in read_env_file(tmp_path / "agents.env")


@respx.mock
async def test_summary_hides_the_token_unless_asked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _mock_stack()

    await run(_argv(tmp_path), _environ(), sleep=_no_sleep)

    out = capsys.readouterr().out
    assert TOKEN not in out
    assert CHANNEL_ID in out
    assert ROOM_UUID in out
    assert "key-loki" not in out


@respx.mock
async def test_print_flag_shows_the_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _mock_stack()

    await run(_argv(tmp_path, "--print"), _environ(), sleep=_no_sleep)

    assert TOKEN in capsys.readouterr().out


@respx.mock
async def test_rerun_keeps_a_working_token_and_registers_nothing_new(tmp_path: Path) -> None:
    first = _mock_stack()
    await run(_argv(tmp_path), _environ(), sleep=_no_sleep)
    tokens_created_first_run = first["token"].call_count
    respx.clear()
    second = _mock_stack()

    code = await run(_argv(tmp_path), _environ(), sleep=_no_sleep)

    assert code == 0
    assert tokens_created_first_run == 1
    assert second["token"].call_count == 0
    assert second["register"].call_count == 0
    assert read_env_file(tmp_path / ".env")["HEIMDALL_SWITCH_TOKEN"] == TOKEN


@respx.mock
async def test_fails_when_the_channel_never_appears_in_switch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _mock_stack(bridged=False)

    code = await run(_argv(tmp_path), _environ(), sleep=_no_sleep)

    assert code == 1
    assert "not bridged" in capsys.readouterr().out
    assert "HEIMDALL_DEFAULT_SWITCH_AGENT_ROOM_ID" not in read_env_file(tmp_path / ".env")


@respx.mock
async def test_fails_clearly_when_a_service_never_becomes_healthy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    respx.get(f"{SWITCH}/health").mock(return_value=httpx.Response(503))
    respx.get(f"{MM_API}/system/ping").mock(return_value=httpx.Response(200, json={}))

    code = await run(_argv(tmp_path), _environ(), sleep=_no_sleep)

    assert code == 1
    assert "Switch" in capsys.readouterr().out


async def test_missing_mattermost_admin_names_the_variable(tmp_path: Path) -> None:
    environ = _environ()
    del environ["MATTERMOST_ADMIN_PASSWORD"]

    with pytest.raises(ConfigInvalid, match="MATTERMOST_ADMIN_PASSWORD"):
        await run(_argv(tmp_path), environ, sleep=_no_sleep)
