"""Agent AI settings inherit defaults and allow provider overrides."""

from agents.heimdall.config import load_ai_config as load_heimdall_config
from agents.huginn.config import load_ai_config as load_huginn_config
from agents.loki.config import load_ai_config as load_loki_config


def test_all_agents_inherit_shared_provider_configuration() -> None:
    """One default configuration constructs settings for each agent."""
    values = {
        "LLM_DEFAULT_PROVIDER": "openai_compat",
        "LLM_DEFAULT_BASE_URL": "https://gateway.example/v1",
        "LLM_DEFAULT_API_KEY": "test-key",
        "LLM_DEFAULT_MODEL": "default-model",
    }
    for load_config in (load_huginn_config, load_loki_config, load_heimdall_config):
        config = load_config(values)
        assert config.provider == "openai_compat"
        assert config.base_url == "https://gateway.example/v1"
        assert config.api_key == "test-key"
        assert config.model == "default-model"


def test_loki_provider_can_change_without_changing_other_agents() -> None:
    """Agent-specific settings override only that agent's shared defaults."""
    values = {
        "LLM_DEFAULT_PROVIDER": "openai_compat",
        "LLM_DEFAULT_BASE_URL": "https://gateway.example/v1",
        "LLM_DEFAULT_API_KEY": "test-key",
        "LLM_DEFAULT_MODEL": "default-model",
        "LLM_LOKI_PROVIDER": "anthropic",
        "LLM_LOKI_BASE_URL": "https://api.anthropic.com",
        "LLM_LOKI_MODEL": "claude-test",
    }
    loki = load_loki_config(values)
    huginn = load_huginn_config(values)
    assert (loki.provider, loki.base_url, loki.model) == (
        "anthropic",
        "https://api.anthropic.com",
        "claude-test",
    )
    assert (huginn.provider, huginn.base_url, huginn.model) == (
        "openai_compat",
        "https://gateway.example/v1",
        "default-model",
    )
    assert "test-key" not in repr(loki)
