"""Turn a tool's result into the message the model reads: cut to budget, wrapped as PR data."""

import math

from agents._budget import CHARS_PER_TOKEN, AgentBudget, fit_to_budget
from agents._guardrails import clean_pr_text, wrap_pr_data, wrapper_overhead
from agents._toolbox import ToolResult
from heimdall.adapters.ai import ToolCall


def result_for_model(
    budget: AgentBudget, call: ToolCall, result: ToolResult, remaining_tokens: int
) -> str:
    """Fit a tool result to the budget, wrapping real tool output as PR data.

    The toolbox's own refusal and error lines are runtime text, so they go back as they are.
    Tool output is cleaned first, then cut with room kept for the wrapper, so the closing tag
    is always the last thing the model reads.

    Args:
        budget: The agent's budget, for the per-tool byte cap.
        call: The tool call the result answers; its name and ``path`` label the wrapper.
        result: What the toolbox returned.
        remaining_tokens: Estimated tokens the conversation still has room for.

    Returns:
        The text to append to the history as the tool's message.
    """
    byte_cap = budget.max_result_bytes.get(call.name)
    if not result.ran or result.failed:
        return fit_to_budget(result.text, remaining_tokens, byte_cap)
    path = _path_argument(call)
    overhead = wrapper_overhead(call.name, path)
    body = fit_to_budget(
        clean_pr_text(result.text),
        remaining_tokens - math.ceil(overhead / CHARS_PER_TOKEN),
        None if byte_cap is None else byte_cap - overhead,
    )
    return wrap_pr_data(call.name, body, path)


def _path_argument(call: ToolCall) -> str | None:
    """Return the file path the model asked for, when the call named one."""
    path = call.arguments.get("path")
    return path if isinstance(path, str) else None
