"""Huginn's name, loop limits and model settings."""

from heimdall.adapters.ai import AiConfig
from heimdall.adapters.switch.agent import AgentConnectionConfig
from heimdall.adapters.switch.agent.credentials import load_agent_credentials, switch_api_url
from heimdall.modules.repro.api import MAX_REPRO_RUNS

AGENT_NAME = "huginn"
MAX_TURNS = 12

__all__ = ["AGENT_NAME", "MAX_REPRO_RUNS", "MAX_TURNS", "load_ai_config", "load_switch_config"]


def load_switch_config(rooms: tuple[str, ...]) -> AgentConnectionConfig:
    """Huginn's Switch connection: SWITCH_HUGINN_AGENT_ID and SWITCH_HUGINN_API_KEY.

    Args:
        rooms: Rooms to claim when the socket opens.

    Returns:
        Connection settings for Huginn's SwitchAgentClient.

    Raises:
        ConfigInvalid: the id or key is unset.
    """
    return load_agent_credentials(AGENT_NAME, switch_api_url(), rooms=rooms)


def load_ai_config() -> AiConfig:
    """Huginn's model settings: LLM_HUGINN_* with LLM_DEFAULT_* fallback.

    Returns:
        Validated settings for Huginn's AiAdapter.

    Raises:
        ConfigInvalid: a required variable is unset or the provider is unknown.
    """
    return AiConfig.from_env(AGENT_NAME)
