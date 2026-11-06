"""A maintainer asking Heimdall to recheck a PR, in any wording, runs the recheck tool."""

from collections.abc import Sequence

from agents._conversation import MentionMode, MentionPr, answer_mention
from agents._reply import ACT_DENIED_REPLY
from agents._toolbox import AgentTool
from agents.heimdall.mention import MENTION_AGENT as HEIMDALL
from tests.unit.agents.scripted_ai import ScriptedAi, tool_reply
from tests.unit.agents.test_conversation import (
    FakePorts,
    _call,  # pyright: ignore[reportPrivateUsage]
    _intent,  # pyright: ignore[reportPrivateUsage]
    _offered_tools,  # pyright: ignore[reportPrivateUsage]
    _question,  # pyright: ignore[reportPrivateUsage]
    _submit,  # pyright: ignore[reportPrivateUsage]
)

WANTS_RECHECK = _submit(in_scope=True, answer="I'll recheck it.", wants_action=True)
RECHECK = tool_reply(_call("recheck_intake", {}))
RECHECKED = _submit(
    in_scope=True,
    answer="Intake is running again on the current head.",
    action_taken="intake_rerun",
)


class RecheckPorts(FakePorts):
    """The shared fake ports plus Heimdall's recheck tool."""

    async def tools(self, pr: MentionPr, mode: MentionMode) -> Sequence[AgentTool]:
        base = await super().tools(pr, mode)
        return (*base, self._tool("recheck_intake", {"rechecked": "yes", "state": "received"}))


async def test_a_maintainers_recheck_runs_the_tool_once_and_says_so() -> None:
    ai = ScriptedAi([WANTS_RECHECK, _intent(True), RECHECK, RECHECKED])
    ports = RecheckPorts()

    answer = await answer_mention(HEIMDALL, ports, ai, _question("@heimdall recheck"))

    assert ports.modes == ["ask", "act"]
    assert ports.ran.count("recheck_intake") == 1
    assert answer.text.startswith("Heimdall: Intake is running again")


async def test_the_recheck_tool_is_offered_only_in_act_mode() -> None:
    ai = ScriptedAi([WANTS_RECHECK, _intent(False)])

    await answer_mention(HEIMDALL, RecheckPorts(), ai, _question("@heimdall what is the state?"))

    assert "recheck_intake" not in _offered_tools(ai)


async def test_a_non_maintainer_cannot_trigger_a_recheck() -> None:
    ai = ScriptedAi([WANTS_RECHECK])
    ports = RecheckPorts(maintainers=("alice",))

    answer = await answer_mention(
        HEIMDALL, ports, ai, _question("@heimdall recheck", sender="mallory")
    )

    assert "recheck_intake" not in ports.ran
    assert ACT_DENIED_REPLY in answer.text


async def test_a_recheck_that_ran_is_reported_even_when_the_summary_fails() -> None:
    no_evidence = _submit(in_scope=True, answer="Done.", action_taken="repro_started")
    ai = ScriptedAi([WANTS_RECHECK, _intent(True), RECHECK, no_evidence, no_evidence])

    answer = await answer_mention(HEIMDALL, RecheckPorts(), ai, _question("@heimdall recheck"))

    assert "re-ran intake" in answer.text
