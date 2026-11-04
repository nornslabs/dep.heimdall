"""An agent's connection id must be the same in its own service and in the worker."""

from uuid import UUID

from heimdall.adapters.switch.agent.credentials import load_agent_credentials

BASE_URL = "http://switch.test"


def _environ(agent: str, agent_id: str) -> dict[str, str]:
    scope = agent.upper()
    return {f"SWITCH_{scope}_AGENT_ID": agent_id, f"SWITCH_{scope}_API_KEY": "key"}


def test_two_processes_of_one_agent_get_the_same_connection_id() -> None:
    environ = _environ("loki", "agent-loki")

    first = load_agent_credentials("loki", BASE_URL, rooms=("r1",), environ=environ)
    second = load_agent_credentials("loki", BASE_URL, rooms=(), environ=environ)

    assert first.connection_id == second.connection_id


def test_different_agents_get_different_connection_ids() -> None:
    loki = load_agent_credentials("loki", BASE_URL, rooms=(), environ=_environ("loki", "a-1"))
    huginn = load_agent_credentials("huginn", BASE_URL, rooms=(), environ=_environ("huginn", "a-2"))

    assert loki.connection_id != huginn.connection_id


def test_connection_id_is_a_uuid_and_does_not_contain_the_api_key() -> None:
    environ = {"SWITCH_LOKI_AGENT_ID": "agent-loki", "SWITCH_LOKI_API_KEY": "secret-key-value"}

    config = load_agent_credentials("loki", BASE_URL, rooms=(), environ=environ)

    assert UUID(config.connection_id)
    assert "secret-key-value" not in config.connection_id
