"""A recheck triage refused reaches the model as a refusal, so it cannot be claimed as done."""

from collections.abc import Mapping

from agents._evidence import REFUSAL_PREFIX
from agents._toolbox import AgentTool
from agents.heimdall._rerun_guard import report_rerun_refusal
from heimdall.adapters.ai import ToolSpec

SCHEMA: Mapping[str, object] = {"type": "object"}


def _checks_tool(result: object) -> AgentTool:
    async def handler(_arguments: Mapping[str, object]) -> object:
        return result

    return AgentTool(ToolSpec("rerun_checks", "recheck", SCHEMA), handler)


async def test_an_accepted_recheck_passes_through_unchanged() -> None:
    accepted = {"rechecked": "yes", "state": "received"}

    result = await report_rerun_refusal(_checks_tool(accepted)).handler({})

    assert result == accepted


async def test_a_refused_recheck_becomes_a_refusal_naming_the_reason() -> None:
    refused = {"rechecked": "no", "reason": "not_allowed_from_state"}

    result = await report_rerun_refusal(_checks_tool(refused)).handler({})

    assert isinstance(result, str)
    assert result.startswith(REFUSAL_PREFIX)
    assert "not_allowed_from_state" in result


async def test_a_result_of_an_unexpected_shape_is_treated_as_refused() -> None:
    result = await report_rerun_refusal(_checks_tool("surprise")).handler({})

    assert isinstance(result, str) and result.startswith(REFUSAL_PREFIX)
