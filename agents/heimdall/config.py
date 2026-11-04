"""Heimdall's name, loop limits and model settings."""

from heimdall.adapters.ai import AiConfig
from heimdall.adapters.switch.agent import AgentConnectionConfig
from heimdall.adapters.switch.agent.credentials import load_agent_credentials, switch_api_url

AGENT_NAME = "heimdall"
MAX_TURNS = 8


def load_switch_config(rooms: tuple[str, ...]) -> AgentConnectionConfig:
    """Heimdall's Switch connection: SWITCH_HEIMDALL_AGENT_ID and SWITCH_HEIMDALL_API_KEY.

    Args:
        rooms: Rooms to claim when the socket opens.

    Returns:
        Connection settings for Heimdall's SwitchAgentClient.

    Raises:
        ConfigInvalid: the id or key is unset.
    """
    return load_agent_credentials(AGENT_NAME, switch_api_url(), rooms=rooms)


def load_ai_config() -> AiConfig:
    """Heimdall's model settings: LLM_HEIMDALL_* with LLM_DEFAULT_* fallback.

    Returns:
        Validated settings for Heimdall's AiAdapter.

    Raises:
        ConfigInvalid: a required variable is unset or the provider is unknown.
    """
    return AiConfig.from_env(AGENT_NAME)
