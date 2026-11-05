"""Loki's name, loop limits and model settings."""

from agents._budget import AgentBudget
from heimdall.adapters.ai import AiConfig
from heimdall.adapters.switch.agent import AgentConnectionConfig
from heimdall.adapters.switch.agent.credentials import load_agent_credentials, switch_api_url

AGENT_NAME = "loki"
REQUIRED_SKILLS: frozenset[str] = frozenset(
    {"edge_cases", "scope_check", "test_quality", "mechanical_tagging"}
)
MAX_TURNS = 8
MAX_FINDINGS = 5
CONTEXT_TOKENS = 40_000
RUN_TOKEN_BUDGET = 250_000
MAX_DIFF_BYTES = 80 * 1024
MAX_FILE_READS = 8

# Starting points; tune on the seeded PRs.
BUDGET = AgentBudget(
    context_tokens=CONTEXT_TOKENS,
    run_tokens=RUN_TOKEN_BUDGET,
    max_turns=MAX_TURNS,
    call_caps={"get_file": MAX_FILE_READS},
    max_result_bytes={"get_diff": MAX_DIFF_BYTES},
)


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
