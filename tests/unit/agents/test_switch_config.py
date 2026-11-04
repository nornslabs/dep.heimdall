"""Each agent builds its Switch connection from its own SWITCH_<AGENT>_* credentials."""

from collections.abc import Callable

import pytest

from agents.heimdall import config as heimdall_config
from agents.huginn import config as huginn_config
from agents.loki import config as loki_config
from heimdall.adapters.switch.agent import AgentConnectionConfig
from heimdall.shared.errors import ConfigInvalid

Loader = Callable[..., AgentConnectionConfig]

CASES = [
    ("huginn", huginn_config.load_switch_config),
    ("loki", loki_config.load_switch_config),
    ("heimdall", heimdall_config.load_switch_config),
]


@pytest.mark.parametrize(("agent", "loader"), CASES)
def test_loads_that_agents_own_id_and_key(
    agent: str, loader: Loader, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SWITCH_API_URL", "http://switch:8000")
    for name in ("huginn", "loki", "heimdall"):
        monkeypatch.setenv(f"SWITCH_{name.upper()}_AGENT_ID", f"id-{name}")
        monkeypatch.setenv(f"SWITCH_{name.upper()}_API_KEY", f"key-{name}")

    config = loader(rooms=("room-1",))

    assert (config.agent_id, config.api_key) == (f"id-{agent}", f"key-{agent}")
    assert (config.base_url, config.rooms) == ("http://switch:8000", ("room-1",))


@pytest.mark.parametrize(("agent", "loader"), CASES)
def test_missing_credentials_name_the_variable(
    agent: str, loader: Loader, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(f"SWITCH_{agent.upper()}_AGENT_ID", raising=False)

    with pytest.raises(ConfigInvalid, match=f"SWITCH_{agent.upper()}_AGENT_ID"):
        loader(rooms=())
