"""Tests for the one-off command that registers Huginn, Loki and Heimdall with Switch."""

import json
import stat
from pathlib import Path

import httpx
import pytest
import respx

from apps.bridge._env_file import merge_env_file
from apps.bridge.register_agents import parse_args, run
from heimdall.shared.errors import ConfigInvalid

BASE_URL = "http://switch:8000"
TOKEN = "registration-token-secret"
KEYS = {"huginn": "key-secret-huginn", "loki": "key-secret-loki", "heimdall": "key-secret-heimdall"}


def _environ(**extra: str) -> dict[str, str]:
    return {"SWITCH_API_URL": BASE_URL, "AGENT_REGISTRATION_TOKEN": TOKEN, **extra}


def _register_route() -> respx.Route:
    """Answer each registration with an id and key derived from the agent name."""

    def answer(request: httpx.Request) -> httpx.Response:
        name = json.loads(request.content)["name"]
        return httpx.Response(201, json={"id": f"id-{name}", "api_key": KEYS[name]})

    return respx.post(f"{BASE_URL}/agents").mock(side_effect=answer)


def _lines(path: Path) -> dict[str, str]:
    pairs = (line.split("=", 1) for line in path.read_text().splitlines() if line)
    return {name: value for name, value in pairs}


def test_parse_args_defaults() -> None:
    args = parse_args([])

    assert args.overwrite is False
    assert args.secrets_file == Path("secrets/switch-agents.env")
    assert args.room_id is None


@respx.mock
async def test_registers_all_three_and_saves_ids_and_keys(tmp_path: Path) -> None:
    _register_route()
    secrets = tmp_path / "agents.env"

    code = await run(["--secrets-file", str(secrets)], _environ())

    assert code == 0
    assert _lines(secrets) == {
        "SWITCH_HUGINN_AGENT_ID": "id-huginn",
        "SWITCH_HUGINN_API_KEY": KEYS["huginn"],
        "SWITCH_LOKI_AGENT_ID": "id-loki",
        "SWITCH_LOKI_API_KEY": KEYS["loki"],
        "SWITCH_HEIMDALL_AGENT_ID": "id-heimdall",
        "SWITCH_HEIMDALL_API_KEY": KEYS["heimdall"],
    }


@respx.mock
async def test_secrets_file_is_readable_only_by_the_owner(tmp_path: Path) -> None:
    _register_route()
    secrets = tmp_path / "agents.env"

    await run(["--secrets-file", str(secrets)], _environ())

    assert stat.S_IMODE(secrets.stat().st_mode) == 0o600


@respx.mock
async def test_output_never_contains_a_key_or_the_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _register_route()

    await run(["--secrets-file", str(tmp_path / "agents.env")], _environ())

    output = capsys.readouterr()
    for secret in (*KEYS.values(), TOKEN):
        assert secret not in output.out + output.err


@respx.mock
async def test_a_duplicate_name_is_a_clear_message_and_the_others_still_register(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        name = json.loads(request.content)["name"]
        if name == "loki":
            return httpx.Response(409, json={"detail": "exists"})
        return httpx.Response(201, json={"id": f"id-{name}", "api_key": KEYS[name]})

    respx.post(f"{BASE_URL}/agents").mock(side_effect=answer)
    secrets = tmp_path / "agents.env"

    code = await run(["--secrets-file", str(secrets)], _environ())

    assert code == 1
    assert "loki" in capsys.readouterr().out
    saved = _lines(secrets)
    assert "SWITCH_HUGINN_API_KEY" in saved
    assert "SWITCH_HEIMDALL_API_KEY" in saved
    assert "SWITCH_LOKI_API_KEY" not in saved


@respx.mock
async def test_the_duplicate_message_tells_the_operator_how_to_rotate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    respx.post(f"{BASE_URL}/agents").mock(return_value=httpx.Response(409))

    await run(["--secrets-file", str(tmp_path / "agents.env")], _environ())

    assert "--overwrite" in capsys.readouterr().out


@respx.mock
async def test_overwrite_flag_rotates_keys(tmp_path: Path) -> None:
    overwrite_flags: list[bool] = []

    def answer(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        overwrite_flags.append(body["overwrite"])
        return httpx.Response(201, json={"id": f"id-{body['name']}", "api_key": KEYS[body["name"]]})

    respx.post(f"{BASE_URL}/agents").mock(side_effect=answer)

    code = await run(["--overwrite", "--secrets-file", str(tmp_path / "agents.env")], _environ())

    assert code == 0
    assert overwrite_flags == [True, True, True]


@respx.mock
async def test_a_failure_on_the_last_agent_keeps_the_first_two_keys(tmp_path: Path) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        name = json.loads(request.content)["name"]
        if name == "heimdall":
            return httpx.Response(503)
        return httpx.Response(201, json={"id": f"id-{name}", "api_key": KEYS[name]})

    respx.post(f"{BASE_URL}/agents").mock(side_effect=answer)
    secrets = tmp_path / "agents.env"

    code = await run(["--secrets-file", str(secrets)], _environ())

    assert code == 1
    assert set(_lines(secrets)) == {
        "SWITCH_HUGINN_AGENT_ID",
        "SWITCH_HUGINN_API_KEY",
        "SWITCH_LOKI_AGENT_ID",
        "SWITCH_LOKI_API_KEY",
    }


@respx.mock
async def test_unrelated_lines_in_the_secrets_file_survive(tmp_path: Path) -> None:
    _register_route()
    secrets = tmp_path / "agents.env"
    secrets.write_text("OTHER_SETTING=keep-me\n")

    await run(["--secrets-file", str(secrets)], _environ())

    assert _lines(secrets)["OTHER_SETTING"] == "keep-me"


@respx.mock
async def test_with_a_room_id_each_new_agent_gets_the_triage_policy(tmp_path: Path) -> None:
    _register_route()
    respx.post(f"{BASE_URL}/gateway/auth/login").mock(
        return_value=httpx.Response(200, json={}, headers={"set-cookie": "sid=abc; Path=/"})
    )
    policy = respx.put(url__regex=rf"{BASE_URL}/gateway/agents/id-\w+/addressing-policy").mock(
        return_value=httpx.Response(200, json={})
    )
    environ = _environ(GATEWAY_ADMIN_EMAIL="admin@example.com", GATEWAY_ADMIN_PASSWORD="pw")

    code = await run(["--room-id", "room-1", "--secrets-file", str(tmp_path / "a.env")], environ)

    assert code == 0
    assert policy.call_count == 3


@respx.mock
async def test_without_a_room_id_addressing_is_skipped_and_no_login_happens(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _register_route()
    login = respx.post(f"{BASE_URL}/gateway/auth/login").mock(return_value=httpx.Response(200))

    code = await run(["--secrets-file", str(tmp_path / "a.env")], _environ())

    assert code == 0
    assert login.call_count == 0
    assert "addressing" in capsys.readouterr().out


async def test_a_missing_registration_token_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigInvalid, match="AGENT_REGISTRATION_TOKEN"):
        await run(["--secrets-file", str(tmp_path / "a.env")], {"SWITCH_API_URL": BASE_URL})


async def test_a_room_id_without_admin_credentials_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigInvalid, match="GATEWAY_ADMIN_EMAIL"):
        await run(["--room-id", "r", "--secrets-file", str(tmp_path / "a.env")], _environ())


def test_merge_env_file_replaces_a_value_and_keeps_the_rest(tmp_path: Path) -> None:
    path = tmp_path / "x.env"
    path.write_text("A=1\nB=2\n")

    merge_env_file(path, {"B": "3", "C": "4"})

    assert _lines(path) == {"A": "1", "B": "3", "C": "4"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def _put_route() -> respx.Route:
    return respx.put(url__regex=rf"{BASE_URL}/gateway/agents/[^/]+/addressing-policy").mock(
        return_value=httpx.Response(200, json={})
    )


def _login_ok() -> respx.Route:
    return respx.post(f"{BASE_URL}/gateway/auth/login").mock(
        return_value=httpx.Response(200, json={}, headers={"set-cookie": "sid=abc; Path=/"})
    )


ADMIN = {"GATEWAY_ADMIN_EMAIL": "admin@example.com", "GATEWAY_ADMIN_PASSWORD": "pw"}


@respx.mock
async def test_an_unwritable_secrets_path_fails_before_any_key_is_issued(tmp_path: Path) -> None:
    register = _register_route()
    blocker = tmp_path / "file"
    blocker.write_text("x")

    with pytest.raises(ConfigInvalid, match="cannot write"):
        await run(["--secrets-file", str(blocker / "agents.env")], _environ())

    assert register.call_count == 0


@respx.mock
async def test_a_write_failure_midway_is_reported_stops_the_run_and_hides_the_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    register = _register_route()
    calls: list[int] = []

    def failing_merge(path: Path, updates: dict[str, str]) -> None:
        calls.append(1)
        # Each agent is saved twice (combined file, then its own), so call 3 is the second agent.
        if len(calls) == 3:
            raise OSError("disk full")
        merge_env_file(path, updates)

    monkeypatch.setattr("apps.bridge.register_agents.merge_env_file", failing_merge)

    code = await run(["--secrets-file", str(tmp_path / "a.env")], _environ())

    out = capsys.readouterr().out
    assert code == 1
    assert "NOT saved" in out
    assert register.call_count == 2
    assert not any(key in out for key in KEYS.values())


@respx.mock
async def test_a_failed_policy_update_keeps_the_key_and_says_how_to_retry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _register_route()
    _login_ok()
    respx.put(url__regex=rf"{BASE_URL}/gateway/agents/[^/]+/addressing-policy").mock(
        return_value=httpx.Response(503)
    )
    secrets = tmp_path / "a.env"

    code = await run(["--room-id", "r", "--secrets-file", str(secrets)], _environ(**ADMIN))

    out = capsys.readouterr().out
    assert code == 1
    assert "addressing failed" in out
    assert "--addressing-only" in out
    assert "SWITCH_LOKI_API_KEY" in _lines(secrets)


@respx.mock
async def test_a_rejected_admin_login_stops_before_any_agent_is_registered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    register = _register_route()
    login = respx.post(f"{BASE_URL}/gateway/auth/login").mock(return_value=httpx.Response(401))

    code = await run(
        ["--room-id", "r", "--secrets-file", str(tmp_path / "a.env")], _environ(**ADMIN)
    )

    assert code == 2
    assert register.call_count == 0
    assert login.call_count == 1
    assert "pw" not in capsys.readouterr().out.split()


@respx.mock
async def test_a_skipped_duplicate_does_not_get_addressing_set(tmp_path: Path) -> None:
    respx.post(f"{BASE_URL}/agents").mock(return_value=httpx.Response(409))
    _login_ok()
    put = _put_route()

    await run(["--room-id", "r", "--secrets-file", str(tmp_path / "a.env")], _environ(**ADMIN))

    assert put.call_count == 0


@respx.mock
async def test_addressing_only_uses_saved_ids_and_never_registers(tmp_path: Path) -> None:
    register = _register_route()
    _login_ok()
    put = _put_route()
    secrets = tmp_path / "a.env"
    merge_env_file(
        secrets,
        {
            "SWITCH_HUGINN_AGENT_ID": "id-huginn",
            "SWITCH_LOKI_AGENT_ID": "id-loki",
            "SWITCH_HEIMDALL_AGENT_ID": "id-heimdall",
        },
    )
    environ = {"SWITCH_API_URL": BASE_URL, **ADMIN}

    code = await run(
        ["--addressing-only", "--room-id", "r", "--secrets-file", str(secrets)], environ
    )

    assert code == 0
    assert register.call_count == 0
    assert put.call_count == 3


@respx.mock
async def test_addressing_only_reports_an_agent_with_no_saved_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _login_ok()
    _put_route()
    secrets = tmp_path / "a.env"
    merge_env_file(secrets, {"SWITCH_HUGINN_AGENT_ID": "id-huginn"})
    environ = {"SWITCH_API_URL": BASE_URL, **ADMIN}

    code = await run(
        ["--addressing-only", "--room-id", "r", "--secrets-file", str(secrets)], environ
    )

    assert code == 1
    assert "loki" in capsys.readouterr().out


async def test_addressing_only_needs_a_room_id(tmp_path: Path) -> None:
    with pytest.raises(ConfigInvalid, match="--room-id"):
        await run(
            ["--addressing-only", "--secrets-file", str(tmp_path / "a.env")], _environ(**ADMIN)
        )


def test_a_blank_room_id_is_rejected_not_treated_as_no_room() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--room-id", "   "])


def test_the_config_repr_hides_the_token_and_admin_password() -> None:
    from apps.bridge.register_agents import load_config

    config = load_config(parse_args(["--room-id", "r"]), _environ(**ADMIN))

    assert TOKEN not in repr(config)
    assert "pw" not in repr(config).replace("password", "")


def test_main_exits_2_on_missing_configuration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from apps.bridge.register_agents import main

    monkeypatch.delenv("AGENT_REGISTRATION_TOKEN", raising=False)
    monkeypatch.setattr("sys.argv", ["register_agents"])

    with pytest.raises(SystemExit) as caught:
        main()

    assert caught.value.code == 2
    assert "AGENT_REGISTRATION_TOKEN" in capsys.readouterr().err


def test_merge_env_file_rejects_control_characters_in_values(tmp_path: Path) -> None:
    for bad in ("a\rb", "a\x00b", "a\x0bb", chr(0x2028).join("ab")):
        with pytest.raises(ValueError):
            merge_env_file(tmp_path / "x.env", {"K": bad})


def test_merge_env_file_creates_the_parent_directory_private(tmp_path: Path) -> None:
    merge_env_file(tmp_path / "secrets" / "x.env", {"K": "v"})

    assert stat.S_IMODE((tmp_path / "secrets").stat().st_mode) == 0o700


@respx.mock
async def test_each_agent_also_gets_a_private_file_holding_only_its_own_key(
    tmp_path: Path,
) -> None:
    _register_route()
    secrets = tmp_path / "agents.env"

    await run(["--secrets-file", str(secrets)], _environ())

    for name in ("huginn", "loki", "heimdall"):
        own = tmp_path / f"switch-{name}.env"
        assert _lines(own) == {
            f"SWITCH_{name.upper()}_AGENT_ID": f"id-{name}",
            f"SWITCH_{name.upper()}_API_KEY": KEYS[name],
        }
        assert stat.S_IMODE(own.stat().st_mode) == 0o600


@respx.mock
async def test_rotating_a_key_updates_that_agents_own_file(tmp_path: Path) -> None:
    _register_route()
    secrets = tmp_path / "agents.env"
    await run(["--secrets-file", str(secrets)], _environ())
    merge_env_file(tmp_path / "switch-loki.env", {"SWITCH_LOKI_API_KEY": "stale"})

    await run(["--secrets-file", str(secrets), "--overwrite"], _environ())

    assert _lines(tmp_path / "switch-loki.env")["SWITCH_LOKI_API_KEY"] == KEYS["loki"]
