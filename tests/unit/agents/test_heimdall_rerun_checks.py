"""Heimdall infers a rerun from the maintainer's message and the PR, not from a command."""

from collections.abc import Sequence

import pytest

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

WANTS_RERUN = _submit(in_scope=True, answer="I'll run the checks again.", wants_action=True)
RERUN = tool_reply(_call("rerun_checks", {}))
RERAN = _submit(
    in_scope=True,
    answer="The checks are running again on the current head.",
    action_taken="checks_rerun",
)
ASKS_FOR_A_RERUN = [
    "@heimdall recheck",
    "@heimdall run it again",
    "@heimdall I pushed a fix, take another look",
    "@heimdall can you re-run everything?",
]
JUST_A_QUESTION = [
    "@heimdall why did it fail?",
    "@heimdall is it good now?",
    "@heimdall what state is this PR in?",
]


class RerunPorts(FakePorts):
    """The shared fake ports plus Heimdall's rerun tool."""

    async def tools(self, pr: MentionPr, mode: MentionMode) -> Sequence[AgentTool]:
        base = await super().tools(pr, mode)
        result = {"rechecked": "yes", "was": "card_posted", "state": "received"}
        return (*base, self._tool("rerun_checks", result))


@pytest.mark.parametrize("message", ASKS_FOR_A_RERUN)
async def test_any_wording_that_asks_for_a_rerun_runs_the_tool_once(message: str) -> None:
    ai = ScriptedAi([WANTS_RERUN, _intent(True), RERUN, RERAN])
    ports = RerunPorts()

    answer = await answer_mention(HEIMDALL, ports, ai, _question(message))

    assert ports.modes == ["ask", "act"]
    assert ports.ran.count("rerun_checks") == 1
    assert answer.text.startswith("Heimdall: The checks are running again")


@pytest.mark.parametrize("message", JUST_A_QUESTION)
async def test_a_question_about_a_result_stays_in_ask_mode(message: str) -> None:
    ai = ScriptedAi([WANTS_RERUN, _intent(False)])
    ports = RerunPorts()

    await answer_mention(HEIMDALL, ports, ai, _question(message))

    assert ports.modes == ["ask"]
    assert "rerun_checks" not in ports.ran
    assert "rerun_checks" not in _offered_tools(ai)


async def test_a_non_maintainer_cannot_trigger_a_rerun() -> None:
    ai = ScriptedAi([WANTS_RERUN])
    ports = RerunPorts(maintainers=("alice",))

    answer = await answer_mention(
        HEIMDALL, ports, ai, _question("@heimdall recheck", sender="mallory")
    )

    assert "rerun_checks" not in ports.ran
    assert ACT_DENIED_REPLY in answer.text


async def test_a_rerun_that_ran_is_reported_even_when_the_summary_fails() -> None:
    no_evidence = _submit(in_scope=True, answer="Done.", action_taken="repro_started")
    ai = ScriptedAi([WANTS_RERUN, _intent(True), RERUN, no_evidence, no_evidence])

    answer = await answer_mention(HEIMDALL, RerunPorts(), ai, _question("@heimdall recheck"))

    assert "re-ran the checks" in answer.text
