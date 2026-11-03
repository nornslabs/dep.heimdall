"""Huginn's name, loop limits and model settings."""

from heimdall.adapters.ai import AiConfig
from heimdall.modules.repro.api import MAX_REPRO_RUNS

AGENT_NAME = "huginn"
MAX_TURNS = 12

__all__ = ["AGENT_NAME", "MAX_REPRO_RUNS", "MAX_TURNS", "load_ai_config"]


def load_ai_config() -> AiConfig:
    """Huginn's model settings: LLM_HUGINN_* with LLM_DEFAULT_* fallback.

    Returns:
        Validated settings for Huginn's AiAdapter.

    Raises:
        ConfigInvalid: a required variable is unset or the provider is unknown.
    """
    return AiConfig.from_env(AGENT_NAME)
