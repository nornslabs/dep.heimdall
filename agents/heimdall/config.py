"""Heimdall's name, loop limits and model settings."""

from heimdall.adapters.ai import AiConfig

AGENT_NAME = "heimdall"
MAX_TURNS = 8


def load_ai_config() -> AiConfig:
    """Heimdall's model settings: LLM_HEIMDALL_* with LLM_DEFAULT_* fallback.

    Returns:
        Validated settings for Heimdall's AiAdapter.

    Raises:
        ConfigInvalid: a required variable is unset or the provider is unknown.
    """
    return AiConfig.from_env(AGENT_NAME)
