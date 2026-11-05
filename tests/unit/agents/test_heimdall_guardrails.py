"""Tests for Heimdall's guardrails: maintainers, draft limits, and finding citations."""

import uuid
from typing import cast

import pytest

from agents._toolbox import AgentTool
from agents.heimdall.guardrails import (
    CommandGuard,
    DeskGuard,
    SavedIdTracker,
    find_quoted_finding_ids,
    guard_create_mechanical_task,
    guard_save_card,
    guard_save_draft,
    submit_desk_result_tool,
)
from agents.heimdall.schemas import DeskResult
from heimdall.adapters.ai import ToolSpec

# --- CommandGuard ---------------------------------------------------------------------


def test_command_guard_allows_listed_maintainer_case_insensitively() -> None:
    guard = CommandGuard(("Alice", "Bob-Smith"))

    assert guard.refusal("alice") is None
    assert guard.refusal("BOB-SMITH") is None


def test_command_guard_refuses_non_maintainer() -> None:
    guard = CommandGuard(("alice", "bob"))

    refusal = guard.refusal("mallory")
    assert refusal is not None
    assert "mallory" in refusal
    assert "not a maintainer" in refusal


# --- find_quoted_finding_ids -----------------------------------------------------------


def test_find_quoted_finding_ids_extracts_various_formats() -> None:
    uid = str(uuid.uuid4())
    text = f"Citing finding-1 and finding: {uid} as well as [finding-42]."

    ids = find_quoted_finding_ids(text)
    assert "finding-1" in ids
    assert uid in ids
    assert "finding-42" in ids


def test_find_quoted_finding_ids_returns_empty_when_no_citations() -> None:
    assert find_quoted_finding_ids("Plain message without citations.") == ()


# --- DeskGuard & tool wrappers -------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_guard_allows_two_drafts_and_refuses_third() -> None:
    guard = DeskGuard(max_drafts=2)
    saved: list[dict[str, object]] = []

    async def fake_handler(arguments: object) -> object:
        saved.append(cast(dict[str, object], arguments))
        return {"draft_id": f"draft-{len(saved)}"}

    tool = AgentTool(ToolSpec("save_draft", "save draft", {"type": "object"}), fake_handler)
    guarded = guard_save_draft(guard, tool)

    res1 = await guarded.handler({"body": "Draft 1"})
    assert res1 == {"draft_id": "draft-1"}
    assert guard.draft_count == 1

    res2 = await guarded.handler({"body": "Draft 2"})
    assert res2 == {"draft_id": "draft-2"}
    assert guard.draft_count == 2

    res3 = await guarded.handler({"body": "Draft 3"})
    assert res3 == "error: save_draft: at most 2 drafts per command"
    assert len(saved) == 2


@pytest.mark.asyncio
async def test_draft_guard_refuses_invented_finding_id() -> None:
    guard = DeskGuard(existing_finding_ids=("finding-1",))

    async def fake_handler(arguments: object) -> object:
        return {"draft_id": "draft-1"}

    tool = AgentTool(ToolSpec("save_draft", "save draft", {"type": "object"}), fake_handler)
    guarded = guard_save_draft(guard, tool)

    # Valid citation passes
    res_valid = await guarded.handler({"body": "Addressing finding-1."})
    assert res_valid == {"draft_id": "draft-1"}

    # Invented citation is refused
    res_invented = await guarded.handler({"body": "Addressing finding-999."})
    assert res_invented == (
        "error: save_draft: finding id 'finding-999' does not exist for this PR head"
    )


@pytest.mark.asyncio
async def test_card_guard_refuses_invented_finding_id_in_gaps() -> None:
    guard = DeskGuard(existing_finding_ids=("finding-1",))

    async def fake_handler(arguments: object) -> object:
        return {"card_id": "card-1"}

    tool = AgentTool(ToolSpec("save_card", "save card", {"type": "object"}), fake_handler)
    guarded = guard_save_card(guard, tool)

    # Valid gaps pass
    res_valid = await guarded.handler({"gaps": ["Notice finding-1 here."]})
    assert res_valid == {"card_id": "card-1"}

    # Invented gap finding is refused
    res_invented = await guarded.handler({"gaps": ["Notice finding-888 here."]})
    assert res_invented == (
        "error: save_card: finding id 'finding-888' does not exist for this PR head"
    )


@pytest.mark.asyncio
async def test_task_guard_refuses_invented_finding_id() -> None:
    guard = DeskGuard(existing_finding_ids=("finding-1",))

    async def fake_handler(arguments: object) -> object:
        return {"task_id": "HT-1-1"}

    tool = AgentTool(ToolSpec("create_mechanical_task", "task", {"type": "object"}), fake_handler)
    guarded = guard_create_mechanical_task(guard, tool)

    # Valid finding_id passes
    res_valid = await guarded.handler({"finding_id": "finding-1"})
    assert res_valid == {"task_id": "HT-1-1"}

    # Invented finding_id is refused
    res_invented = await guarded.handler({"finding_id": "finding-unknown"})
    assert res_invented == (
        "error: create_mechanical_task: finding_id 'finding-unknown' "
        "does not exist for this PR head"
    )


# --- SavedIdTracker & submit_desk_result_tool -----------------------------------------


@pytest.mark.asyncio
async def test_submit_desk_result_tool_cross_check_accepts_saved_ids() -> None:
    tracker = SavedIdTracker()
    tracker.record("card-42")
    tool = submit_desk_result_tool(tracker)

    result = DeskResult(action="card", card_id="card-42")
    problems = await tool.verify(result)
    assert problems is None


@pytest.mark.asyncio
async def test_submit_desk_result_tool_cross_check_refuses_unsaved_ids() -> None:
    tracker = SavedIdTracker()
    tracker.record("card-1")
    tool = submit_desk_result_tool(tracker)

    result = DeskResult(action="card", card_id="card-999")
    problems = await tool.verify(result)
    assert problems is not None
    assert "card-999 was not saved" in problems.errors
