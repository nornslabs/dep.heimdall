"""Tests for reading one agent's Switch id and key from SWITCH_<AGENT>_* variables."""

import pytest

from heimdall.adapters.switch.agent.credentials import (
    DEFAULT_SWITCH_API_URL,
    credential_names,
    load_agent_credentials,
    switch_api_url,
)
from heimdall.shared.errors import ConfigInvalid

SWITCH_URL = "http://switch:8000"


def test_credential_names_are_upper_cased_per_agent() -> None:
    assert credential_names("huginn") == ("SWITCH_HUGINN_AGENT_ID", "SWITCH_HUGINN_API_KEY")


def test_loads_id_and_key_into_a_connection_config() -> None:
    environ = {"SWITCH_LOKI_AGENT_ID": "agent-2", "SWITCH_LOKI_API_KEY": "key-2"}

    config = load_agent_credentials("loki", SWITCH_URL, rooms=("room-1",), environ=environ)

    assert (config.base_url, config.agent_id, config.api_key) == (SWITCH_URL, "agent-2", "key-2")
    assert config.rooms == ("room-1",)


@pytest.mark.parametrize("missing", ["SWITCH_LOKI_AGENT_ID", "SWITCH_LOKI_API_KEY"])
def test_a_missing_variable_is_named_in_the_error(missing: str) -> None:
    environ = {"SWITCH_LOKI_AGENT_ID": "agent-2", "SWITCH_LOKI_API_KEY": "key-2"}
    del environ[missing]

    with pytest.raises(ConfigInvalid, match=missing):
        load_agent_credentials("loki", SWITCH_URL, rooms=(), environ=environ)


def test_a_blank_value_counts_as_unset() -> None:
    environ = {"SWITCH_LOKI_AGENT_ID": "agent-2", "SWITCH_LOKI_API_KEY": "   "}

    with pytest.raises(ConfigInvalid, match="SWITCH_LOKI_API_KEY"):
        load_agent_credentials("loki", SWITCH_URL, rooms=(), environ=environ)


def test_the_key_never_appears_in_the_config_repr() -> None:
    environ = {"SWITCH_LOKI_AGENT_ID": "agent-2", "SWITCH_LOKI_API_KEY": "key-secret"}

    config = load_agent_credentials("loki", SWITCH_URL, rooms=(), environ=environ)

    assert "key-secret" not in repr(config)


def test_switch_api_url_reads_the_variable_and_trims_a_trailing_slash() -> None:
    assert switch_api_url({"SWITCH_API_URL": " http://switch:8000/ "}) == "http://switch:8000"


@pytest.mark.parametrize("environ", [{}, {"SWITCH_API_URL": "  "}])
def test_switch_api_url_falls_back_to_the_local_default(environ: dict[str, str]) -> None:
    assert switch_api_url(environ) == DEFAULT_SWITCH_API_URL


@pytest.mark.parametrize(
    "url",
    [
        "http://switch:8000",
        "http://localhost:8010",
        "http://127.0.0.1:8000",
        "https://switch.example.com",
    ],
)
def test_switch_api_url_accepts_safe_urls(url: str) -> None:
    assert switch_api_url({"SWITCH_API_URL": url}) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://user:pass@switch:8000",
        "ftp://switch:8000",
        "switch:8000",
        "http://switch.example.com",
        "https://",
    ],
)
def test_switch_api_url_rejects_unsafe_urls_without_echoing_them(url: str) -> None:
    with pytest.raises(ConfigInvalid) as caught:
        switch_api_url({"SWITCH_API_URL": url})

    assert url not in str(caught.value)
