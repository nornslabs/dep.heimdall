"""Huginn on the shared runtime: the model picks the steps, the limits stay fixed.

Huginn wakes on ``pr.ready_for_repro`` and finishes by calling ``submit_verdict``. What it may
do lives in ``config.py`` and in its guardrails; what it did lives in its verdict.
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
from agents.huginn.config import AGENT_NAME, BUDGET
from agents.huginn.schemas import HuginnVerdict
from heimdall.adapters.ai import ChatMessage


async def run_huginn(
    *,
    ai: ChatModel,
    toolbox: ToolBox,
    submit: SubmitTool[HuginnVerdict],
    head_sha: str,
    read_current_head: Callable[[], Awaitable[str]],
    messages: Sequence[ChatMessage],
    budget: AgentBudget = BUDGET,
) -> RunOutcome[HuginnVerdict]:
    """Let Huginn's model choose its path until it submits a verdict or a limit stops it.

    Args:
        ai: Huginn's model, built from ``LLM_HUGINN_*``.
        toolbox: Its allowed tools, already wrapped by ``ReproGuard``.
        submit: The verdict tool, bound to the cross-check against saved runs.
        head_sha: PR head this run is about.
        read_current_head: Reads the PR head again before a verdict is accepted.
        messages: The prompt and the PR brief to start from.
        budget: Turn, token, call and result-size limits.

    Returns:
        The run's outcome; only ``Completed`` carries a verdict worth posting.
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
