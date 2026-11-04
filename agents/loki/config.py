"""Loki's name, loop limits and model settings."""

from heimdall.adapters.ai import AiConfig
from heimdall.adapters.switch.agent import AgentConnectionConfig
from heimdall.adapters.switch.agent.credentials import load_agent_credentials, switch_api_url

AGENT_NAME = "loki"
MAX_TURNS = 8
MAX_FINDINGS = 5


def load_switch_config(rooms: tuple[str, ...]) -> AgentConnectionConfig:
    """Loki's Switch connection: SWITCH_LOKI_AGENT_ID and SWITCH_LOKI_API_KEY.

    Args:
        rooms: Rooms to claim when the socket opens.

    Returns:
        Connection settings for Loki's SwitchAgentClient.

    Raises:
        ConfigInvalid: the id or key is unset.
    """
    return load_agent_credentials(AGENT_NAME, switch_api_url(), rooms=rooms)


def load_ai_config() -> AiConfig:
    """Loki's model settings: LLM_LOKI_* with LLM_DEFAULT_* fallback.

    Returns:
        Validated settings for Loki's AiAdapter.

    Raises:
        ConfigInvalid: a required variable is unset or the provider is unknown.
    """
    return AiConfig.from_env(AGENT_NAME)
