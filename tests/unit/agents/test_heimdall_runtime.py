"""Heimdall on the runtime: card assembly, draft limits, and finding citation checks."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import cast

from agents._outcome import Completed, RunOutcome
from agents._runtime import AgentBudget, ToolBox
from agents._skills import load_skills
from agents._toolbox import AgentTool
from agents.heimdall.agent import run_heimdall
from agents.heimdall.allowed_tools import ALLOWED_TOOLS
from agents.heimdall.config import BUDGET, REQUIRED_SKILLS
from agents.heimdall.context import build_desk_brief, build_messages
from agents.heimdall.guardrails import (
    DeskGuard,
    SavedIdTracker,
    guard_create_mechanical_task,
    guard_save_card,
    guard_save_draft,
    submit_desk_result_tool,
)
from agents.heimdall.schemas import DeskResult
from heimdall.adapters.ai import ChatResponse, ToolCall, ToolSpec
from heimdall.shared.agents import AgentName
from tests.e2e.fake_world import ReproScript, load_seeded_pr
from tests.unit.agents.scripted_ai import ScriptedAi, tool_reply

INSTALLATION_ID = 42
PR_ID = "00000000-0000-0000-0000-000000000002"
REPO_FULL_NAME = "nornslabs/swaitchai-fastapi"


class FakeDeskTools:
    """Serve Heimdall's tools, keeping every saved card and draft."""

    def __init__(self, *, head_sha: str, number: int) -> None:
        self.head_sha = head_sha
        self.number = number
        self.saved_cards: list[dict[str, object]] = []
        self.saved_drafts: list[dict[str, object]] = []
        self.saved_tasks: list[dict[str, object]] = []

    async def get_pr_context(self, arguments: Mapping[str, object]) -> object:
        return {
            "pr_id": PR_ID,
            "repo_id": 7,
            "number": self.number,
            "head_sha": self.head_sha,
            "title": "Example PR",
            "body": "",
        }

    async def get_repro(self, arguments: Mapping[str, object]) -> object:
        return {"status": "finished", "verdict": "fix_confirmed"}

    async def list_findings(self, arguments: Mapping[str, object]) -> object:
        return {
            "result": [
                {
                    "id": "finding-1",
                    "file": "switch_agent/config.py",
                    "line": 3,
                    "text": "Unused import",
                    "severity": "warning",
                    "mechanical_category": "lint",
                }
            ]
        }

    async def save_card(self, arguments: Mapping[str, object]) -> object:
        self.saved_cards.append(dict(arguments))
        card_id = f"card-{len(self.saved_cards)}"
        return {"card_id": card_id}

    async def save_draft(self, arguments: Mapping[str, object]) -> object:
        self.saved_drafts.append(dict(arguments))
        draft_id = f"draft-{len(self.saved_drafts)}"
        return {"draft_id": draft_id, "body_hash": "a" * 64}

    async def create_mechanical_task(self, arguments: Mapping[str, object]) -> object:
        self.saved_tasks.append(dict(arguments))
        task_id = f"HT-{self.number}-{len(self.saved_tasks)}"
        return {"task_id": task_id, "suggestion": "Fix it", "task_block": "```"}


def _tool(name: str, handler: Callable[[Mapping[str, object]], Awaitable[object]]) -> AgentTool:
    return AgentTool(ToolSpec(name, f"{name} tool.", {"type": "object"}), handler)


def _head_reader(head_sha: str) -> Callable[[], Awaitable[str]]:
    async def read() -> str:
        return head_sha

    return read


def _track(tool: AgentTool, tracker: SavedIdTracker, key: str) -> AgentTool:
    async def handler(arguments: Mapping[str, object]) -> object:
        res: object = await tool.handler(arguments)
        if isinstance(res, dict):
            res_dict = cast(dict[str, object], res)
            val = res_dict.get(key)
            if isinstance(val, str):
                tracker.record(val)
            return res_dict
        return res

    return AgentTool(tool.spec, handler)


async def _run(
    world: FakeDeskTools,
    replies: Sequence[ChatResponse],
    *,
    existing_finding_ids: Sequence[str] = ("finding-1",),
    command_text: str = "",
    budget: AgentBudget = BUDGET,
) -> tuple[RunOutcome[DeskResult], SavedIdTracker, DeskGuard]:
    tracker = SavedIdTracker()
    guard = DeskGuard(existing_finding_ids=existing_finding_ids)
    skills = load_skills(AgentName.HEIMDALL, ALLOWED_TOOLS, REQUIRED_SKILLS)

    tools = (
        _tool("get_pr_context", world.get_pr_context),
        _tool("get_repro", world.get_repro),
        _tool("list_findings", world.list_findings),
        guard_save_card(guard, _track(_tool("save_card", world.save_card), tracker, "card_id")),
        guard_save_draft(guard, _track(_tool("save_draft", world.save_draft), tracker, "draft_id")),
        guard_create_mechanical_task(
            guard,
            _track(
                _tool("create_mechanical_task", world.create_mechanical_task),
                tracker,
                "task_id",
            ),
        ),
        skills.tool(),
    )
    toolbox = ToolBox(ALLOWED_TOOLS | {"load_skill"}, tools, retry_delays=())
    brief = build_desk_brief(
        installation_id=INSTALLATION_ID,
        pr_id=PR_ID,
        repo_full_name=REPO_FULL_NAME,
        number=world.number,
        title="Example PR",
        head_sha=world.head_sha,
        repro_summary="Fix confirmed.",
        finding_summaries=("[finding-1] switch_agent/config.py:3 — Unused import",),
        command_text=command_text,
    )
    outcome = await run_heimdall(
        ai=ScriptedAi(replies),
        toolbox=toolbox,
        submit=submit_desk_result_tool(tracker),
        head_sha=world.head_sha,
        read_current_head=_head_reader(world.head_sha),
        messages=build_messages(brief, "You are Heimdall.", skills.prompt_index()),
        budget=budget,
    )
    return outcome, tracker, guard


async def test_card_run_saves_card_and_submits_it() -> None:
    world = FakeDeskTools(head_sha="head123", number=7)
    replies = [
        tool_reply(
            ToolCall(
                "c1",
                "save_card",
                {
                    "installation_id": INSTALLATION_ID,
                    "pr_id": PR_ID,
                    "number": 7,
                    "title": "Example PR",
                    "head_sha": "head123",
                    "rules_summary": "All intake rules passed.",
                    "fix_status": "Fix confirmed.",
                    "gaps": ["[finding-1] switch_agent/config.py:3 — Unused import"],
                    "suggested_action": "Review card",
                },
            )
        ),
        tool_reply(ToolCall("c2", "submit_result", {"action": "card", "card_id": "card-1"})),
    ]

    outcome, tracker, _guard = await _run(world, replies)

    assert isinstance(outcome, Completed)
    assert outcome.payload.action == "card"
    assert outcome.payload.card_id == "card-1"
    assert tracker.saved_ids() == ("card-1",)
    assert len(world.saved_cards) == 1


async def test_draft_run_saves_draft_and_submits_it() -> None:
    world = FakeDeskTools(head_sha="head123", number=7)
    replies = [
        tool_reply(
            ToolCall(
                "c1",
                "save_draft",
                {
                    "installation_id": INSTALLATION_ID,
                    "pr_id": PR_ID,
                    "head_sha": "head123",
                    "kind": "thanks",
                    "body": "Thank you for the contribution!",
                },
            )
        ),
        tool_reply(ToolCall("c2", "submit_result", {"action": "draft", "draft_id": "draft-1"})),
    ]

    outcome, tracker, _guard = await _run(world, replies, command_text="@heimdall thank")

    assert isinstance(outcome, Completed)
    assert outcome.payload.action == "draft"
    assert outcome.payload.draft_id == "draft-1"
    assert tracker.saved_ids() == ("draft-1",)


async def test_third_draft_is_refused_and_model_submits_second() -> None:
    world = FakeDeskTools(head_sha="head123", number=7)
    draft_args = {
        "installation_id": INSTALLATION_ID,
        "pr_id": PR_ID,
        "head_sha": "head123",
        "kind": "review",
        "body": "Draft text",
    }
    replies = [
        tool_reply(ToolCall("c1", "save_draft", draft_args)),
        tool_reply(ToolCall("c2", "save_draft", draft_args)),
        # 3rd save_draft will return error: save_draft: at most 2 drafts per command
        tool_reply(ToolCall("c3", "save_draft", draft_args)),
        # Model finishes by submitting draft-2
        tool_reply(ToolCall("c4", "submit_result", {"action": "draft", "draft_id": "draft-2"})),
    ]

    outcome, tracker, guard = await _run(world, replies, command_text="@heimdall ask changes")

    assert isinstance(outcome, Completed)
    assert outcome.payload.action == "draft"
    assert outcome.payload.draft_id == "draft-2"
    assert len(world.saved_drafts) == 2
    assert guard.draft_count == 2
    assert tracker.saved_ids() == ("draft-1", "draft-2")


async def test_submit_with_unsaved_card_id_gets_repair_round() -> None:
    world = FakeDeskTools(head_sha="head123", number=7)
    replies = [
        tool_reply(
            ToolCall(
                "c1",
                "save_card",
                {
                    "installation_id": INSTALLATION_ID,
                    "pr_id": PR_ID,
                    "number": 7,
                    "title": "Example PR",
                    "head_sha": "head123",
                    "rules_summary": "Pass",
                    "fix_status": "Confirmed",
                    "gaps": [],
                    "suggested_action": "Review",
                },
            )
        ),
        # First submission names an unsaved card id
        tool_reply(ToolCall("c2", "submit_result", {"action": "card", "card_id": "card-999"})),
        # Repair round: model submits the real card id
        tool_reply(ToolCall("c3", "submit_result", {"action": "card", "card_id": "card-1"})),
    ]

    outcome, _tracker, _guard = await _run(world, replies)

    assert isinstance(outcome, Completed)
    assert outcome.payload.card_id == "card-1"
    assert outcome.turns == 3


async def test_explanation_needs_no_saved_id() -> None:
    world = FakeDeskTools(head_sha="head123", number=7)
    replies = [
        tool_reply(
            ToolCall(
                "c1",
                "submit_result",
                {
                    "action": "explanation",
                    "explanation": "The reproduction passed because the tests are comprehensive.",
                },
            )
        ),
    ]

    outcome, tracker, _guard = await _run(world, replies, command_text="@heimdall why")

    assert isinstance(outcome, Completed)
    assert outcome.payload.action == "explanation"
    assert outcome.payload.explanation == (
        "The reproduction passed because the tests are comprehensive."
    )
    assert tracker.saved_ids() == ()


async def test_card_citing_invented_finding_id_is_refused_and_recovers() -> None:
    world = FakeDeskTools(head_sha="head123", number=7)
    replies = [
        # First call cites invented finding-999
        tool_reply(
            ToolCall(
                "c1",
                "save_card",
                {
                    "installation_id": INSTALLATION_ID,
                    "pr_id": PR_ID,
                    "number": 7,
                    "title": "Example PR",
                    "head_sha": "head123",
                    "rules_summary": "Pass",
                    "fix_status": "Confirmed",
                    "gaps": ["finding-999: Invented defect"],
                    "suggested_action": "Review",
                },
            )
        ),
        # Model corrects itself and cites existing finding-1
        tool_reply(
            ToolCall(
                "c2",
                "save_card",
                {
                    "installation_id": INSTALLATION_ID,
                    "pr_id": PR_ID,
                    "number": 7,
                    "title": "Example PR",
                    "head_sha": "head123",
                    "rules_summary": "Pass",
                    "fix_status": "Confirmed",
                    "gaps": ["finding-1: Valid defect"],
                    "suggested_action": "Review",
                },
            )
        ),
        tool_reply(ToolCall("c3", "submit_result", {"action": "card", "card_id": "card-1"})),
    ]

    outcome, _tracker, _guard = await _run(world, replies, existing_finding_ids=("finding-1",))

    assert isinstance(outcome, Completed)
    assert outcome.payload.card_id == "card-1"
    assert len(world.saved_cards) == 1
    assert world.saved_cards[0]["gaps"] == ["finding-1: Valid defect"]


async def test_seeded_pr_card_cites_only_existing_finding_ids() -> None:
    """Acceptance criterion: on seeded PRs, Heimdall's card cites only existing finding ids."""
    pr = load_seeded_pr("fake_fix", ReproScript(with_fix_passed=True, without_fix_passed=True))
    world = FakeDeskTools(head_sha=pr.head_sha, number=pr.number)
    replies = [
        tool_reply(
            ToolCall(
                "c1",
                "save_card",
                {
                    "installation_id": INSTALLATION_ID,
                    "pr_id": PR_ID,
                    "number": pr.number,
                    "title": pr.title,
                    "head_sha": pr.head_sha,
                    "rules_summary": "All intake rules passed.",
                    "fix_status": "Issue not reproduced: tests passed with and without the fix.",
                    "gaps": ["[finding-1] switch_agent/config.py:3 — Unused import"],
                    "suggested_action": "Review the card, then approve the comment to post it.",
                },
            )
        ),
        tool_reply(ToolCall("c2", "submit_result", {"action": "card", "card_id": "card-1"})),
    ]

    outcome, tracker, _guard = await _run(world, replies, existing_finding_ids=("finding-1",))

    assert isinstance(outcome, Completed)
    assert outcome.payload.action == "card"
    assert outcome.payload.card_id == "card-1"
    assert tracker.saved_ids() == ("card-1",)
