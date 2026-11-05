"""Heimdall's name, loop limits and model settings."""

from agents._budget import AgentBudget
from heimdall.adapters.ai import AiConfig
from heimdall.adapters.switch.agent import AgentConnectionConfig
from heimdall.adapters.switch.agent.credentials import load_agent_credentials, switch_api_url

AGENT_NAME = "heimdall"
REQUIRED_SKILLS: frozenset[str] = frozenset(
    {
        "write_card",
        "draft_change_request",
        "draft_thanks",
        "draft_close",
        "explain_why",
        "mechanical_task",
    }
)
MAX_TURNS = 8
CONTEXT_TOKENS = 24_000
RUN_TOKEN_BUDGET = 100_000
# At most 2 drafts per command; enforced by the budget's call_caps.
MAX_DRAFTS = 2
# No diff or file access: Heimdall writes the card from other agents' stored results.
NO_CALLS = 0

# Starting points; tune on the seeded PRs.
BUDGET = AgentBudget(
    context_tokens=CONTEXT_TOKENS,
    run_tokens=RUN_TOKEN_BUDGET,
    max_turns=MAX_TURNS,
    call_caps={
        "get_diff": NO_CALLS,
        "get_file": NO_CALLS,
        "save_draft": MAX_DRAFTS,
        "draft_close": MAX_DRAFTS,
        "draft_reopen": MAX_DRAFTS,
        "draft_merge": MAX_DRAFTS,
        "draft_ask_contributor": MAX_DRAFTS,
    },
)

__all__ = [
    "AGENT_NAME",
    "BUDGET",
    "MAX_DRAFTS",
    "MAX_TURNS",
    "REQUIRED_SKILLS",
    "load_ai_config",
    "load_switch_config",
]


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
