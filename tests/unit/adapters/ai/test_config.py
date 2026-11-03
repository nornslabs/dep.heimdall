"""AiConfig reads per-agent LLM settings with a shared default and hides the key."""

import os

import pytest

from heimdall.adapters.ai import AiConfig
from heimdall.shared.errors import ConfigInvalid

DEFAULTS = {
    "LLM_DEFAULT_BASE_URL": "https://openrouter.ai/api/v1",
    "LLM_DEFAULT_API_KEY": "default-secret",
    "LLM_DEFAULT_MODEL": "default-model",
}


@pytest.fixture(autouse=True)
def clean_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("LLM_"):
            monkeypatch.delenv(name)


def _set(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_default_values_apply_when_agent_has_no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, DEFAULTS)

    config = AiConfig.from_env("loki")

    assert config.provider == "openai_compat"
    assert config.base_url == "https://openrouter.ai/api/v1"
    assert config.model == "default-model"
    assert config.api_key.get_secret_value() == "default-secret"


def test_per_agent_value_beats_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(
        monkeypatch,
        {
            **DEFAULTS,
            "LLM_LOKI_BASE_URL": "https://api.groq.com/openai/v1",
            "LLM_LOKI_MODEL": "loki-model",
            "LLM_LOKI_PROVIDER": "anthropic",
        },
    )

    loki = AiConfig.from_env("loki")
    huginn = AiConfig.from_env("huginn")

    assert (loki.base_url, loki.model, loki.provider) == (
        "https://api.groq.com/openai/v1",
        "loki-model",
        "anthropic",
    )
    assert loki.api_key.get_secret_value() == "default-secret"
    assert huginn.model == "default-model"


def test_missing_value_names_both_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, {k: v for k, v in DEFAULTS.items() if k != "LLM_DEFAULT_MODEL"})

    with pytest.raises(ConfigInvalid, match="LLM_LOKI_MODEL or LLM_DEFAULT_MODEL"):
        AiConfig.from_env("loki")


def test_blank_value_counts_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, {**DEFAULTS, "LLM_DEFAULT_API_KEY": "  "})

    with pytest.raises(ConfigInvalid, match="LLM_HUGINN_API_KEY or LLM_DEFAULT_API_KEY"):
        AiConfig.from_env("huginn")


def test_api_key_is_hidden_from_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, DEFAULTS)

    config = AiConfig.from_env("heimdall")

    assert "default-secret" not in repr(config)
    assert "default-secret" not in str(config)


def test_unknown_provider_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, {**DEFAULTS, "LLM_DEFAULT_PROVIDER": "carrier-pigeon"})

    with pytest.raises(ConfigInvalid, match="carrier-pigeon"):
        AiConfig.from_env("loki")
