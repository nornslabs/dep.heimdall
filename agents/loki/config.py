"""Loki's name, loop limits and model settings."""

from heimdall.adapters.ai import AiConfig

AGENT_NAME = "loki"
MAX_TURNS = 8
MAX_FINDINGS = 5


def load_ai_config() -> AiConfig:
    """Loki's model settings: LLM_LOKI_* with LLM_DEFAULT_* fallback.

    Returns:
        Validated settings for Loki's AiAdapter.

    Raises:
        ConfigInvalid: a required variable is unset or the provider is unknown.
    """
    return AiConfig.from_env(AGENT_NAME)
