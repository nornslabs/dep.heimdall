"""Huginn's name, loop limits and model settings."""

from agents._budget import AgentBudget
from heimdall.adapters.ai import AiConfig
from heimdall.adapters.switch.agent import AgentConnectionConfig
from heimdall.adapters.switch.agent.credentials import load_agent_credentials, switch_api_url
from heimdall.modules.repro.api import MAX_REPRO_RUNS

AGENT_NAME = "huginn"
REQUIRED_SKILLS: frozenset[str] = frozenset(
    {"pick_test_command", "judge_repro", "fix_setup_failure"}
)
MAX_TURNS = 12
CONTEXT_TOKENS = 24_000
RUN_TOKEN_BUDGET = 150_000
MAX_DIFF_BYTES = 40 * 1024
MAX_FILE_READS = 4

# Starting points; tune on the seeded PRs.
BUDGET = AgentBudget(
    context_tokens=CONTEXT_TOKENS,
    run_tokens=RUN_TOKEN_BUDGET,
    max_turns=MAX_TURNS,
    call_caps={"start_repro": MAX_REPRO_RUNS, "get_file": MAX_FILE_READS},
    max_result_bytes={"get_diff": MAX_DIFF_BYTES},
)

__all__ = [
    "AGENT_NAME",
    "BUDGET",
    "MAX_REPRO_RUNS",
    "MAX_TURNS",
    "REQUIRED_SKILLS",
    "load_ai_config",
    "load_switch_config",
]


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
