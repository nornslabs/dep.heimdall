"""Make a recheck that triage refused look like what it is: a call that did not run."""

from collections.abc import Mapping
from typing import Final, cast

from agents._evidence import REFUSAL_PREFIX
from agents._toolbox import AgentTool

_ACCEPTED: Final = "yes"
_UNKNOWN_REASON: Final = "unexpected result"


def report_rerun_refusal(tool: AgentTool) -> AgentTool:
    """Return the recheck tool with a refusal reported as a guard's ``error:`` line.

    The evidence tracker counts any other result as an action that ran, so without this the
    model could report a rerun that triage never queued.

    Args:
        tool: The ``rerun_checks`` tool from Heimdall's server.

    Returns:
        The same tool whose handler returns ``error: recheck refused: <reason>`` unless triage
        accepted the recheck.
    """
    original = tool.handler

    async def handler(arguments: Mapping[str, object]) -> object:
        result = await original(arguments)
        fields = _as_fields(result)
        if fields.get("rechecked") == _ACCEPTED:
            return result
        return f"{REFUSAL_PREFIX} recheck refused: {_reason(fields)}"

    return AgentTool(tool.spec, handler)


def _as_fields(result: object) -> Mapping[str, object]:
    """View a tool result as its fields; anything that is not an object has none."""
    if isinstance(result, dict):
        return cast(Mapping[str, object], result)
    return {}


def _reason(fields: Mapping[str, object]) -> str:
    """Pull the reason triage gave out of a refused result."""
    reason = fields.get("reason")
    if isinstance(reason, str) and reason:
        return reason
    return _UNKNOWN_REASON
