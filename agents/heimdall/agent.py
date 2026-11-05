"""Heimdall on the shared runtime: the model picks the steps, the limits stay fixed.

Heimdall wakes on ``pr.ready_for_card`` and ``maintainer.command``. It finishes by calling
``submit_result``. What it may do lives in ``config.py``, ``allowed_tools.py`` and its
guardrails; what it did lives in its ``DeskResult``.
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
from agents.heimdall.config import AGENT_NAME, BUDGET
from agents.heimdall.schemas import DeskResult
from heimdall.adapters.ai import ChatMessage


async def run_heimdall(
    *,
    ai: ChatModel,
    toolbox: ToolBox,
    submit: SubmitTool[DeskResult],
    head_sha: str,
    read_current_head: Callable[[], Awaitable[str]],
    messages: Sequence[ChatMessage],
    budget: AgentBudget = BUDGET,
) -> RunOutcome[DeskResult]:
    """Let Heimdall's model choose its path until it submits a result or a limit stops it.

    Args:
        ai: Heimdall's model, built from ``LLM_HEIMDALL_*``.
        toolbox: Its allowed tools, already wrapped by the saved-id tracker.
        submit: The result tool, bound to the cross-check against saved artifact ids.
        head_sha: PR head this run is about.
        read_current_head: Reads the PR head again before a result is accepted.
        messages: The prompt and the PR brief to start from.
        budget: Turn, token, call and result-size limits.

    Returns:
        The run's outcome; only ``Completed`` carries a result worth posting.
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
