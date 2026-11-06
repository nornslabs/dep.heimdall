"""The evidence check: a reply may cite only what this run's tools returned."""

from collections.abc import Mapping

import pytest

from agents._evidence import EvidenceTracker, reply_problems
from agents._reply import AgentReply
from agents._toolbox import AgentTool
from heimdall.adapters.ai import ToolSpec
from heimdall.shared.agents import AgentName

EMPTY_SCHEMA: Mapping[str, object] = {"type": "object"}


def _tool(name: str, result: object) -> AgentTool:
    async def handler(_arguments: Mapping[str, object]) -> object:
        return result

    return AgentTool(ToolSpec(name, name, EMPTY_SCHEMA), handler)


def _reply(**overrides: object) -> AgentReply:
    fields: dict[str, object] = {"in_scope": True, "answer": "ok", "evidence": ["repro:R-3"]}
    return AgentReply.model_validate({**fields, **overrides})


async def _tracker_that_saw(*tools: AgentTool) -> EvidenceTracker:
    tracker = EvidenceTracker()
    for tool in tools:
        await tracker.wrap(tool).handler({})
    return tracker


async def test_evidence_returned_by_a_tool_is_accepted() -> None:
    tracker = await _tracker_that_saw(
        _tool("get_repro", {"id": "R-3", "status": "finished"}),
        _tool("get_file", "src/pager.py\n91: return page"),
    )
    reply = _reply(evidence=["repro:R-3", "src/pager.py:91"])

    assert reply_problems(reply, tracker, agent=AgentName.HUGINN, mode="ask") == ()


async def test_evidence_no_tool_returned_is_reported() -> None:
    tracker = await _tracker_that_saw(_tool("get_repro", {"id": "R-3"}))
    reply = _reply(evidence=["repro:R-3", "finding:F-99", "src/invented.py:1"])

    problems = reply_problems(reply, tracker, agent=AgentName.HUGINN, mode="ask")

    assert len(problems) == 2
    assert any("finding:F-99" in problem for problem in problems)
    assert any("src/invented.py:1" in problem for problem in problems)


async def test_an_id_that_only_appears_inside_a_longer_id_is_not_evidence() -> None:
    tracker = await _tracker_that_saw(_tool("get_repro", {"id": "R-31", "finding": "F-12"}))
    reply = _reply(evidence=["repro:R-3", "finding:F-1"])

    problems = reply_problems(reply, tracker, agent=AgentName.HUGINN, mode="ask")

    assert len(problems) == 2


async def test_a_path_inside_a_longer_path_still_counts() -> None:
    tracker = await _tracker_that_saw(_tool("get_file", {"path": "/repo/src/pager.py"}))

    reply = _reply(evidence=["src/pager.py:3"])

    assert reply_problems(reply, tracker, agent=AgentName.HUGINN, mode="ask") == ()


async def test_a_redirect_to_the_agent_itself_is_reported() -> None:
    reply = _reply(in_scope=False, redirect_to="loki", answer="", evidence=[])

    problems = reply_problems(reply, EvidenceTracker(), agent=AgentName.LOKI, mode="ask")

    assert problems == ("redirect_to: you cannot redirect to yourself",)


async def test_ask_mode_cannot_report_an_action() -> None:
    tracker = await _tracker_that_saw(_tool("get_repro", {"id": "R-3"}))
    reply = _reply(action_taken="repro_started")

    problems = reply_problems(reply, tracker, agent=AgentName.HUGINN, mode="ask")

    assert problems == ("action_taken: ask mode cannot act; use 'none'",)


async def test_act_mode_cannot_report_an_action_that_did_not_run() -> None:
    tracker = await _tracker_that_saw(_tool("get_repro", {"id": "R-3"}))
    reply = _reply(action_taken="repro_started")

    problems = reply_problems(reply, tracker, agent=AgentName.HUGINN, mode="act")

    assert problems == ("action_taken: 'repro_started' but no matching tool ran",)


async def test_act_mode_must_report_the_action_that_ran() -> None:
    tracker = await _tracker_that_saw(
        _tool("get_repro", {"id": "R-3"}), _tool("start_repro", {"run_id": "R-4"})
    )

    problems = reply_problems(_reply(), tracker, agent=AgentName.HUGINN, mode="act")

    assert problems == ("action_taken: report 'repro_started'; that tool ran",)


async def test_act_mode_accepts_the_action_that_ran() -> None:
    tracker = await _tracker_that_saw(_tool("start_repro", {"run_id": "R-4"}))
    reply = _reply(evidence=["repro:R-4"], action_taken="repro_started")

    assert reply_problems(reply, tracker, agent=AgentName.HUGINN, mode="act") == ()


async def test_a_tool_that_raised_is_not_counted_as_seen_or_run() -> None:
    async def failing(_arguments: Mapping[str, object]) -> object:
        raise RuntimeError("down")

    tracker = EvidenceTracker()
    wrapped = tracker.wrap(AgentTool(ToolSpec("start_repro", "s", EMPTY_SCHEMA), failing))
    with pytest.raises(RuntimeError):
        await wrapped.handler({})

    assert tracker.actions() == frozenset()


async def test_a_write_the_guard_refused_is_not_an_action_taken() -> None:
    tracker = await _tracker_that_saw(_tool("start_repro", "error: runner 'rm' is not allowed"))
    reply = _reply(action_taken="repro_started")

    problems = reply_problems(reply, tracker, agent=AgentName.HUGINN, mode="act")

    assert tracker.actions() == frozenset()
    assert "action_taken: 'repro_started' but no matching tool ran" in problems


async def test_a_recheck_is_accepted_only_after_the_recheck_tool_ran() -> None:
    reply = _reply(evidence=[], action_taken="checks_rerun")
    ran = await _tracker_that_saw(_tool("rerun_checks", {"rechecked": "yes"}))

    assert reply_problems(reply, ran, agent=AgentName.HEIMDALL, mode="act") == ()


async def test_a_claimed_recheck_that_never_ran_is_refused() -> None:
    reply = _reply(evidence=[], action_taken="checks_rerun")
    nothing_ran = await _tracker_that_saw()

    problems = reply_problems(reply, nothing_ran, agent=AgentName.HEIMDALL, mode="act")

    assert any("checks_rerun" in problem for problem in problems)


async def test_a_recheck_cannot_be_claimed_in_ask_mode() -> None:
    reply = _reply(evidence=[], action_taken="checks_rerun")
    tracker = await _tracker_that_saw(_tool("rerun_checks", {"rechecked": "yes"}))

    problems = reply_problems(reply, tracker, agent=AgentName.HEIMDALL, mode="ask")

    assert any("ask mode" in problem for problem in problems)


async def test_a_refused_recheck_does_not_count_as_having_run() -> None:
    reply = _reply(evidence=[], action_taken="checks_rerun")
    refused = await _tracker_that_saw(_tool("rerun_checks", "error: refused by guard"))

    problems = reply_problems(reply, refused, agent=AgentName.HEIMDALL, mode="act")

    assert any("checks_rerun" in problem for problem in problems)
