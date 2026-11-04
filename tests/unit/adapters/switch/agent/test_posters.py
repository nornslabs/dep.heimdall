"""Tests for building one poster per agent whose Switch key is configured."""

import pytest

from heimdall.adapters.switch.agent.posters import build_agent_posters
from heimdall.shared.agents import AgentName
from heimdall.shared.errors import ConfigInvalid

SWITCH_URL = "http://switch:8000"


def _environ(*agents: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for agent in agents:
        values[f"SWITCH_{agent.upper()}_AGENT_ID"] = f"{agent}-id"
        values[f"SWITCH_{agent.upper()}_API_KEY"] = f"{agent}-key"
    return values


def test_each_configured_agent_gets_a_poster() -> None:
    posters = build_agent_posters(SWITCH_URL, _environ("huginn", "loki"))

    assert set(posters) == {AgentName.HUGINN, AgentName.LOKI}


def test_no_configured_agents_gives_no_posters() -> None:
    assert build_agent_posters(SWITCH_URL, {}) == {}


@pytest.mark.parametrize("present", ["SWITCH_LOKI_AGENT_ID", "SWITCH_LOKI_API_KEY"])
def test_half_configured_agent_fails_at_startup_naming_the_missing_variable(present: str) -> None:
    environ = {present: "value"}
    missing = ({"SWITCH_LOKI_AGENT_ID", "SWITCH_LOKI_API_KEY"} - {present}).pop()

    with pytest.raises(ConfigInvalid, match=missing):
        build_agent_posters(SWITCH_URL, environ)


def test_error_never_repeats_a_secret_value() -> None:
    with pytest.raises(ConfigInvalid) as raised:
        build_agent_posters(SWITCH_URL, {"SWITCH_LOKI_API_KEY": "super-secret-key"})

    assert "super-secret-key" not in str(raised.value)
