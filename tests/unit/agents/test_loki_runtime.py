"""Loki on the runtime: the fake-fix seeded PR, diff citations, docs-only and call caps."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import cast

from agents._outcome import Completed, RunOutcome
from agents._runtime import AgentBudget, ToolBox
from agents._skills import load_skills
from agents._toolbox import AgentTool
from agents.loki._diff import diff_added_lines
from agents.loki._message import render_review
from agents.loki.agent import run_loki
from agents.loki.allowed_tools import ALLOWED_TOOLS
from agents.loki.config import BUDGET, REQUIRED_SKILLS
from agents.loki.context import build_messages, build_review_brief
from agents.loki.guardrails import (
    FindingGuard,
    guard_save_findings,
    is_docs_only,
    submit_review_tool,
)
from agents.loki.schemas import LokiReview
from heimdall.adapters.ai import ChatResponse, ToolCall, ToolSpec
from heimdall.shared.agents import AgentName
from tests.e2e.fake_world import ReproScript, SeededPr, load_seeded_pr
from tests.unit.agents.scripted_ai import ScriptedAi, tool_reply

INSTALLATION_ID = 42
PR_ID = "00000000-0000-0000-0000-000000000002"
REPO_FULL_NAME = "nornslabs/swaitchai-fastapi"
SKIP_PATHS = ("docs/**", "*.md")
ADDED_FILE = "switch_agent/config.py"
SUMMARY = "One unused import."
DOCS_DIFF = (
    "diff --git a/docs/guide.md b/docs/guide.md\n"
    "--- a/docs/guide.md\n"
    "+++ b/docs/guide.md\n"
    "@@ -1,2 +1,3 @@\n"
    " # Guide\n"
    "+More words.\n"
)


class FakeReviewTools:
    """Serve Loki's tools the way the MCP server does, keeping every saved finding."""

    def __init__(self, diff_text: str, *, head_sha: str, number: int) -> None:
        """Hold the diff the fake returns and start with nothing saved or read."""
        self.diff_text = diff_text
        self.head_sha = head_sha
        self.number = number
        self.saved: list[dict[str, object]] = []
        self.reads: list[str] = []

    async def get_pr_context(self, arguments: Mapping[str, object]) -> object:
        """Return the scoped PR metadata the MCP tool returns."""
        return {
            "pr_id": PR_ID,
            "repo_id": 7,
            "number": self.number,
            "head_sha": self.head_sha,
            "title": "Example",
            "body": "",
        }

    async def get_diff(self, arguments: Mapping[str, object]) -> object:
        """Return this run's diff."""
        return {"text": self.diff_text, "truncated": False}

    async def get_file(self, arguments: Mapping[str, object]) -> object:
        """Return a small file and remember the read."""
        path = str(arguments.get("path"))
        self.reads.append(path)
        return {"found": True, "path": path, "content": "def example() -> None:\n"}

    async def save_findings(self, arguments: Mapping[str, object]) -> object:
        """Accept every finding the guard allowed and give each one an id."""
        findings = cast(list[dict[str, object]], arguments["findings"])
        ids: list[str] = []
        for finding in findings:
            self.saved.append(finding)
            ids.append(f"finding-{len(self.saved)}")
        return {
            "saved": len(findings),
            "finding_ids": ids,
            "rejected": [],
            "head_sha": arguments["head_sha"],
        }


def _tool(name: str, handler: Callable[[Mapping[str, object]], Awaitable[object]]) -> AgentTool:
    """Describe one fake MCP tool with a permissive schema, so only the logic is tested."""
    return AgentTool(ToolSpec(name, f"{name} tool.", {"type": "object"}), handler)


def _head_reader(head_sha: str) -> Callable[[], Awaitable[str]]:
    """Return a head check that never moves."""

    async def read() -> str:
        return head_sha

    return read


def _finding(line: int, *, file: str = ADDED_FILE) -> dict[str, object]:
    """Build one well-formed finding for the seeded PR."""
    return {
        "file": file,
        "line": line,
        "text": "`re` is imported but never used.",
        "severity": "warning",
        "mechanical_category": "lint",
    }


def _save(*findings: dict[str, object], head_sha: str) -> dict[str, object]:
    """Build the arguments one ``save_findings`` call carries."""
    return {
        "installation_id": INSTALLATION_ID,
        "pr_id": PR_ID,
        "repo_full_name": REPO_FULL_NAME,
        "head_sha": head_sha,
        "findings": list(findings),
    }


def _submit(**values: object) -> dict[str, object]:
    """Build the arguments one ``submit_review`` call carries."""
    return {"summary": SUMMARY, **values}


def _changed_paths(pr: SeededPr) -> list[str]:
    """Read the recorded file list for a seeded PR."""
    rows = cast(list[dict[str, object]], pr.read_json("files.json"))
    return [str(row["filename"]) for row in rows]


def _fake_fix() -> SeededPr:
    """The seeded PR whose tests pass with and without the change."""
    return load_seeded_pr("fake_fix", ReproScript(with_fix_passed=True, without_fix_passed=True))


def _world(pr: SeededPr, diff_text: str | None = None) -> FakeReviewTools:
    """Build the fake tools for one seeded PR."""
    return FakeReviewTools(
        diff_text if diff_text is not None else pr.read("pull.diff"),
        head_sha=pr.head_sha,
        number=pr.number,
    )


async def _run(
    pr: SeededPr,
    world: FakeReviewTools,
    replies: Sequence[ChatResponse],
    *,
    changed_paths: Sequence[str] | None = None,
    budget: AgentBudget = BUDGET,
) -> tuple[RunOutcome[LokiReview], FindingGuard]:
    """Run Loki once on a seeded PR, with a scripted model and its real toolbox."""
    paths = list(changed_paths) if changed_paths is not None else _changed_paths(pr)
    guard = FindingGuard(
        diff_lines=diff_added_lines(world.diff_text),
        docs_only=is_docs_only(paths, SKIP_PATHS),
    )
    skills = load_skills(AgentName.LOKI, ALLOWED_TOOLS, REQUIRED_SKILLS)
    toolbox = ToolBox(
        ALLOWED_TOOLS | {"load_skill"},
        (
            _tool("get_pr_context", world.get_pr_context),
            _tool("get_diff", world.get_diff),
            _tool("get_file", world.get_file),
            guard_save_findings(guard, _tool("save_findings", world.save_findings)),
            skills.tool(),
        ),
        retry_delays=(),
    )
    brief = build_review_brief(
        installation_id=INSTALLATION_ID,
        pr_id=PR_ID,
        repo_full_name=REPO_FULL_NAME,
        number=pr.number,
        title=pr.title,
        body=str(pr.pull["body"]),
        head_sha=pr.head_sha,
        base_sha=pr.base_sha,
        changed_paths=paths,
        skip_paths=SKIP_PATHS,
    )
    outcome = await run_loki(
        ai=ScriptedAi(replies),
        toolbox=toolbox,
        submit=submit_review_tool(guard),
        head_sha=pr.head_sha,
        read_current_head=_head_reader(pr.head_sha),
        messages=build_messages(brief, "You are Loki.", skills.prompt_index()),
        budget=budget,
    )
    return outcome, guard


async def test_fake_fix_review_saves_a_cited_finding_and_submits_it() -> None:
    """The acceptance case: at least one finding, cited by an added line, reaches the review."""
    pr = _fake_fix()
    world = _world(pr)
    replies = [
        tool_reply(ToolCall("c1", "save_findings", _save(_finding(3), head_sha=pr.head_sha))),
        tool_reply(ToolCall("c2", "submit_review", _submit(finding_ids=["finding-1"]))),
    ]

    outcome, guard = await _run(pr, world, replies)

    assert isinstance(outcome, Completed)
    assert outcome.payload.finding_ids == ("finding-1",)
    assert [finding["line"] for finding in world.saved] == [3]
    assert guard.saved_ids() == ("finding-1",)
    assert render_review(outcome.payload, head_sha=pr.head_sha).startswith(
        f"Loki: reviewed {pr.head_sha[:7]}. 1 finding saved."
    )


async def test_a_citation_outside_the_diff_is_refused_and_the_model_recovers() -> None:
    """Line 2 is context, not an added line, so the save is refused and nothing is stored."""
    pr = _fake_fix()
    world = _world(pr)
    replies = [
        tool_reply(ToolCall("c1", "save_findings", _save(_finding(2), head_sha=pr.head_sha))),
        tool_reply(ToolCall("c2", "save_findings", _save(_finding(3), head_sha=pr.head_sha))),
        tool_reply(ToolCall("c3", "submit_review", _submit(finding_ids=["finding-1"]))),
    ]

    outcome, guard = await _run(pr, world, replies)

    assert isinstance(outcome, Completed)
    assert [finding["line"] for finding in world.saved] == [3]
    assert guard.saved_ids() == ("finding-1",)


async def test_a_citation_to_an_unchanged_file_is_refused() -> None:
    """A file the PR did not change has no added lines to cite."""
    pr = _fake_fix()
    world = _world(pr)
    replies = [
        tool_reply(
            ToolCall(
                "c1",
                "save_findings",
                _save(_finding(1, file="src/untouched.py"), head_sha=pr.head_sha),
            )
        ),
        tool_reply(ToolCall("c2", "submit_review", _submit(skipped_reason="no_findings"))),
    ]

    outcome, _guard = await _run(pr, world, replies)

    assert isinstance(outcome, Completed)
    assert outcome.payload.skipped_reason == "no_findings"
    assert world.saved == []


async def test_a_docs_only_pr_is_skipped_without_saving() -> None:
    """Every changed path is skipped, so the guard refuses the save and the review says so."""
    pr = _fake_fix()
    world = _world(pr, DOCS_DIFF)
    replies = [
        tool_reply(
            ToolCall(
                "c1",
                "save_findings",
                _save(_finding(3, file="docs/guide.md"), head_sha=pr.head_sha),
            )
        ),
        tool_reply(ToolCall("c2", "submit_review", _submit(skipped_reason="docs_only"))),
    ]

    outcome, _guard = await _run(pr, world, replies, changed_paths=["docs/guide.md"])

    assert isinstance(outcome, Completed)
    assert outcome.payload.skipped_reason == "docs_only"
    assert world.saved == []
    assert render_review(outcome.payload, head_sha=pr.head_sha).startswith(
        f"Loki: reviewed {pr.head_sha[:7]}. Docs-only change; no findings."
    )


async def test_a_docs_only_pr_must_submit_the_docs_only_reason() -> None:
    """A reviewable-looking skip reason gets one repair round, then the right one is accepted."""
    pr = _fake_fix()
    world = _world(pr, DOCS_DIFF)
    replies = [
        tool_reply(ToolCall("c1", "submit_review", _submit(skipped_reason="no_findings"))),
        tool_reply(ToolCall("c2", "submit_review", _submit(skipped_reason="docs_only"))),
    ]

    outcome, _guard = await _run(pr, world, replies, changed_paths=["docs/guide.md"])

    assert isinstance(outcome, Completed)
    assert outcome.payload.skipped_reason == "docs_only"
    assert outcome.turns == 2


async def test_a_reviewable_pr_cannot_claim_docs_only() -> None:
    """A PR with a source change cannot be skipped as docs-only."""
    pr = _fake_fix()
    world = _world(pr)
    replies = [
        tool_reply(ToolCall("c1", "submit_review", _submit(skipped_reason="docs_only"))),
        tool_reply(ToolCall("c2", "submit_review", _submit(skipped_reason="no_findings"))),
    ]

    outcome, _guard = await _run(pr, world, replies)

    assert isinstance(outcome, Completed)
    assert outcome.payload.skipped_reason == "no_findings"


async def test_a_review_that_cites_an_unsaved_id_gets_one_repair_round() -> None:
    """The cross-check refuses an id this run did not save, then accepts the corrected review."""
    pr = _fake_fix()
    world = _world(pr)
    replies = [
        tool_reply(ToolCall("c1", "submit_review", _submit(finding_ids=["finding-9"]))),
        tool_reply(ToolCall("c2", "submit_review", _submit(skipped_reason="no_findings"))),
    ]

    outcome, _guard = await _run(pr, world, replies)

    assert isinstance(outcome, Completed)
    assert outcome.payload.skipped_reason == "no_findings"
    assert outcome.turns == 2


async def test_the_ninth_file_read_is_refused() -> None:
    """The budget allows eight file reads; the ninth never reaches the tool."""
    pr = _fake_fix()
    world = _world(pr)
    reads = tuple(
        ToolCall(f"read-{index}", "get_file", {"path": f"src/file_{index}.py"})
        for index in range(1, 10)
    )
    replies = [
        tool_reply(*reads),
        tool_reply(ToolCall("c-submit", "submit_review", _submit(skipped_reason="no_findings"))),
    ]

    outcome, _guard = await _run(pr, world, replies)

    assert isinstance(outcome, Completed)
    assert len(world.reads) == 8
