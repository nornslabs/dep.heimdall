"""Loki's guardrails: added-line citations, the docs-only rule and the saved-id record."""

from collections.abc import Mapping
from typing import cast

from agents._toolbox import AgentTool
from agents.loki._diff import diff_added_lines
from agents.loki.guardrails import (
    FindingGuard,
    guard_save_findings,
    is_docs_only,
)
from agents.loki.schemas import LokiReview
from heimdall.adapters.ai import ToolSpec
from tests.e2e.fake_world import ReproScript, load_seeded_pr

SKIP_PATHS = ("docs/**", "*.md")
ADDED_FILE = "switch_agent/config.py"
TEST_FILE = "tests/test_ws_url.py"


def _fake_fix_diff() -> str:
    """Return the recorded fake-fix diff, whose added lines the tests cite."""
    return load_seeded_pr(
        "fake_fix", ReproScript(with_fix_passed=True, without_fix_passed=True)
    ).read("pull.diff")


def _guard(diff_text: str | None = None, *, docs_only: bool = False) -> FindingGuard:
    """Build a guard for the recorded diff, with no saved findings yet."""
    return FindingGuard(
        diff_lines=diff_added_lines(diff_text if diff_text is not None else _fake_fix_diff()),
        docs_only=docs_only,
    )


def _finding(line: int, *, file: str = ADDED_FILE) -> dict[str, object]:
    """Build one well-formed finding for the recorded PR."""
    return {
        "file": file,
        "line": line,
        "text": "`re` is imported but never used.",
        "severity": "warning",
    }


def _save(*findings: dict[str, object]) -> dict[str, object]:
    """Build the arguments one ``save_findings`` call carries."""
    return {"findings": list(findings)}


def _saved_tool(guard: FindingGuard, seen: list[Mapping[str, object]]) -> AgentTool:
    """Build a fake ``save_findings`` that reports one saved id per finding."""

    async def handler(arguments: Mapping[str, object]) -> object:
        seen.append(arguments)
        findings = arguments.get("findings")
        count = len(cast(list[object], findings)) if isinstance(findings, list) else 0
        return {
            "saved": count,
            "finding_ids": [f"finding-{index}" for index in range(1, count + 1)],
            "rejected": [],
        }

    return guard_save_findings(guard, AgentTool(ToolSpec("save_findings", "save", {}), handler))


# --- diff index --------------------------------------------------------------------------


def test_diff_index_holds_the_new_side_of_every_added_line() -> None:
    """A finding may cite an added line, and the removed lines have no new-side number."""
    added = diff_added_lines(_fake_fix_diff())

    assert added[ADDED_FILE] == frozenset({3, 85, 86, 87})
    assert added[TEST_FILE] == frozenset(range(1, 10))


def test_a_deleted_file_has_no_lines_to_cite() -> None:
    """A file the PR removes has no new version, so no citation can point into it."""
    diff = "diff --git a/old.py b/old.py\n--- a/old.py\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-gone\n"

    assert diff_added_lines(diff) == {}


def test_the_no_newline_marker_is_not_a_line() -> None:
    """The ``\\ No newline`` marker must not shift or add a citation."""
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,1 +1,2 @@\n"
        " kept\n"
        "+added\n"
        "\\ No newline at end of file\n"
    )

    assert diff_added_lines(diff) == {"a.py": frozenset({2})}


def test_added_content_shaped_like_a_header_stays_content() -> None:
    """A line that begins with ``+++`` inside a hunk is an added line, not a new file."""
    diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,2 @@\n kept\n+++marker\n"

    assert diff_added_lines(diff) == {"a.py": frozenset({2})}


# --- docs-only ---------------------------------------------------------------------------


def test_a_pr_touching_only_skipped_paths_is_docs_only() -> None:
    assert is_docs_only(["docs/guide.md", "README.md"], SKIP_PATHS) is True


def test_one_source_file_makes_the_pr_reviewable() -> None:
    assert is_docs_only(["docs/guide.md", "src/app.py"], SKIP_PATHS) is False


def test_an_empty_change_set_is_not_docs_only() -> None:
    assert is_docs_only([], SKIP_PATHS) is False


# --- save guard --------------------------------------------------------------------------


def test_a_finding_on_an_added_line_may_be_saved() -> None:
    assert _guard().refusal(_save(_finding(3))) is None


def test_a_finding_on_a_context_line_is_refused() -> None:
    """Line 2 of the changed file is context, so the finding cannot cite it."""
    refusal = _guard().refusal(_save(_finding(2)))

    assert refusal is not None
    assert "not added lines" in refusal
    assert f"{ADDED_FILE}:2" in refusal
    assert "imported but never used" not in refusal


def test_a_finding_outside_every_changed_file_is_refused() -> None:
    refusal = _guard().refusal(_save(_finding(1, file="src/untouched.py")))

    assert refusal is not None
    assert "src/untouched.py:1" in refusal


def test_a_docs_only_pr_may_save_nothing() -> None:
    refusal = _guard(docs_only=True).refusal(_save(_finding(3)))

    assert refusal is not None
    assert "skipped paths" in refusal
    assert "docs_only" in refusal


def test_a_finding_without_a_usable_location_is_refused() -> None:
    """No path or line means no citation, so the finding cannot be saved."""
    refusal = _guard().refusal({"findings": [{"line": "three"}]})

    assert refusal is not None
    assert "(missing file:line)" in refusal


async def test_a_refused_call_never_reaches_the_tool() -> None:
    guard = _guard()
    seen: list[Mapping[str, object]] = []
    tool = _saved_tool(guard, seen)

    result = await tool.handler(_save(_finding(2)))

    assert isinstance(result, str)
    assert result.startswith("error: ")
    assert seen == []


async def test_an_allowed_call_records_the_ids_it_saved() -> None:
    guard = _guard()
    seen: list[Mapping[str, object]] = []
    tool = _saved_tool(guard, seen)

    result = await tool.handler(_save(_finding(3)))

    assert isinstance(result, dict)
    assert guard.saved_ids() == ("finding-1",)
    assert seen != []


# --- submit checks -----------------------------------------------------------------------


def _review(**overrides: object) -> LokiReview:
    """Build a valid review, staying valid unless a test overrides a value."""
    values: dict[str, object] = {"finding_ids": ["finding-1"], "summary": "One unused import."}
    values.update(overrides)
    return LokiReview.model_validate(values)


def test_a_saved_id_has_no_problem() -> None:
    guard = _guard()
    guard.record({"finding_ids": ["finding-1"], "rejected": []})

    assert guard.submission_problems(_review()) == ()


def test_an_id_this_run_did_not_save_is_named() -> None:
    problems = _guard().submission_problems(_review())

    assert problems == (
        "finding_ids: finding-1 was not saved through save_findings for this PR head",
    )


def test_a_docs_only_pr_must_be_submitted_as_docs_only() -> None:
    problems = _guard(docs_only=True).submission_problems(
        _review(finding_ids=[], skipped_reason="no_findings")
    )

    assert problems == (
        "skipped_reason: this PR only changes skipped paths, so it must be submitted as docs_only",
    )


def test_a_reviewable_pr_cannot_claim_docs_only() -> None:
    problems = _guard().submission_problems(_review(finding_ids=[], skipped_reason="docs_only"))

    assert problems == (
        "skipped_reason: this PR changes paths outside skip_paths, so docs_only does not apply",
    )
