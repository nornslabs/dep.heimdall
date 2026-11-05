"""Loki on the shared runtime: the model chooses what to read, the limits stay fixed.

Loki wakes on ``pr.ready_for_review`` and finishes by calling ``submit_review``. What it may do
lives in ``config.py``, ``allowed_tools.py`` and its guardrails; what it found lives in its
review.
"""

from collections.abc import Awaitable, Callable, Sequence

from agents._runtime import (
    AgentBudget,
    AgentRun,
    ChatModel,
    RunOutcome,
    SubmitTool,
    ToolBox,
    run_agent,
)
from agents.loki.config import AGENT_NAME, BUDGET
from agents.loki.schemas import LokiReview
from heimdall.adapters.ai import ChatMessage


async def run_loki(
    *,
    ai: ChatModel,
    toolbox: ToolBox,
    submit: SubmitTool[LokiReview],
    head_sha: str,
    read_current_head: Callable[[], Awaitable[str]],
    messages: Sequence[ChatMessage],
    budget: AgentBudget = BUDGET,
) -> RunOutcome[LokiReview]:
    """Let Loki's model choose its path until it submits a review or a limit stops it.

    Args:
        ai: Loki's model, built from ``LLM_LOKI_*``.
        toolbox: Its allowed tools, already wrapped by ``FindingGuard``.
        submit: The review tool, bound to the citation and docs-only checks.
        head_sha: PR head this run is about.
        read_current_head: Reads the PR head again before a review is accepted.
        messages: The prompt and the PR brief to start from.
        budget: Turn, token, call and result-size limits.

    Returns:
        The run's outcome; only ``Completed`` carries a review worth posting.
    """
    run = AgentRun(
        agent=AGENT_NAME,
        ai=ai,
        toolbox=toolbox,
        submit=submit,
        budget=budget,
        head_sha=head_sha,
        read_current_head=read_current_head,
    )
    return await run_agent(run, messages)
