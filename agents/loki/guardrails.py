"""Loki's own guardrails on a review: citations must be added lines, docs-only PRs are skipped.

The runtime bounds turns, tokens, call counts and context. These rules are about what a review
may claim: every finding points at a line this PR added, and a PR that only touches skipped
paths gets no findings at all. A refused ``save_findings`` reaches the model as an ``error: ...``
result, so it can correct itself instead of crashing the job.
"""

from collections.abc import Mapping, Sequence
from fnmatch import fnmatchcase
from typing import cast

from agents._submit import SubmitTool
from agents._toolbox import AgentTool
from agents.loki.schemas import SUBMIT_REVIEW, LokiReview, review_problems

MAX_REFUSED_CITATIONS = 5
"""Most refused ``file:line`` pairs named in one error line."""
MISSING_CITATION = "(missing file:line)"
"""What a finding without a usable path and line is called in the refusal."""


def is_docs_only(changed_paths: Sequence[str], skip_paths: Sequence[str]) -> bool:
    """Say whether every path a PR changes matches a review skip pattern.

    Args:
        changed_paths: Every path the PR touches, repository-relative.
        skip_paths: ``review.skip_paths`` from the repository's validated config.

    Returns:
        True only when there is at least one changed path and all of them are skipped.
    """
    if not changed_paths:
        return False
    return all(_skipped(path, skip_paths) for path in changed_paths)


class FindingGuard:
    """Keep a run's citations inside the diff and remember what ``save_findings`` accepted."""

    def __init__(self, *, diff_lines: Mapping[str, frozenset[int]], docs_only: bool) -> None:
        """Bind this run's added lines, the docs-only decision and an empty record.

        Args:
            diff_lines: Added new-side lines per changed path, from ``diff_added_lines``.
            docs_only: True when every changed path is a skipped path.
        """
        self._diff_lines = diff_lines
        self._docs_only = docs_only
        self._saved_ids: list[str] = []
        self._rejected = 0

    @property
    def rejected_count(self) -> int:
        """How many findings the review module refused across the run's saves."""
        return self._rejected

    def saved_ids(self) -> tuple[str, ...]:
        """Return the finding ids ``save_findings`` accepted during this run."""
        return tuple(self._saved_ids)

    def refusal(self, arguments: Mapping[str, object]) -> str | None:
        """Say why this ``save_findings`` call must be refused, or None when it may run.

        Args:
            arguments: The model's parsed arguments for ``save_findings``.

        Returns:
            One line naming the rule broken, or None. A docs-only PR saves nothing, and every
            finding must carry a path and cite an added line in this PR's diff.
        """
        if self._docs_only:
            return (
                "save_findings: this PR only changes skipped paths; save nothing and submit "
                "skipped_reason 'docs_only'"
            )
        outside = _outside_the_diff(arguments.get("findings"), self._diff_lines)
        if not outside:
            return None
        return (
            "save_findings: these citations are not added lines in this PR's diff: "
            f"{_shown(outside)}; cite a changed line or do not report it"
        )

    def record(self, result: object) -> None:
        """Remember the ids and rejections a successful ``save_findings`` reported."""
        if not isinstance(result, dict):
            return
        values = cast(dict[str, object], result)
        ids = values.get("finding_ids")
        if isinstance(ids, list):
            self._saved_ids.extend(
                value for value in cast(list[object], ids) if isinstance(value, str)
            )
        rejected = values.get("rejected")
        if isinstance(rejected, list):
            self._rejected += len(cast(list[object], rejected))

    def submission_problems(self, review: LokiReview) -> tuple[str, ...]:
        """List where a submitted review breaks the skip rule or cites an unsaved id.

        Args:
            review: The submission that passed schema validation.

        Returns:
            One message per problem, empty when the review stands.
        """
        problems: list[str] = []
        if self._docs_only and review.skipped_reason != "docs_only":
            problems.append(
                "skipped_reason: this PR only changes skipped paths, so it must be "
                "submitted as docs_only"
            )
        if not self._docs_only and review.skipped_reason == "docs_only":
            problems.append(
                "skipped_reason: this PR changes paths outside skip_paths, so docs_only "
                "does not apply"
            )
        problems.extend(review_problems(review, self._saved_ids))
        return tuple(problems)


def guard_save_findings(guard: FindingGuard, save: AgentTool) -> AgentTool:
    """Wrap ``save_findings`` so a refused call reaches the model as an error result.

    Args:
        guard: The run's guard, holding the diff and the saved-id record.
        save: The toolbox tool that validates and stores findings, unchanged when allowed.

    Returns:
        A tool with the same spec whose handler checks the guard first and records what the
        successful call accepted.
    """

    async def handler(arguments: Mapping[str, object]) -> object:
        refusal = guard.refusal(arguments)
        if refusal is not None:
            return f"error: {refusal}"
        result = await save.handler(arguments)
        guard.record(result)
        return result

    return AgentTool(save.spec, handler)


def submit_review_tool(guard: FindingGuard) -> SubmitTool[LokiReview]:
    """Build Loki's submit tool with the skip rule and saved-id check bound to this run.

    Args:
        guard: The run's guard, holding the docs-only decision and the saved ids.

    Returns:
        The tool whose cross-check turns a review the run cannot support into the model's
        one repair round.
    """

    async def cross_check(review: LokiReview) -> tuple[str, ...]:
        return guard.submission_problems(review)

    return SubmitTool(
        SUBMIT_REVIEW.name,
        LokiReview,
        SUBMIT_REVIEW.spec.description,
        cross_check=cross_check,
    )


def _skipped(path: str, skip_paths: Sequence[str]) -> bool:
    """Say whether one changed path matches any review skip pattern."""
    return any(fnmatchcase(path, pattern) for pattern in skip_paths)


def _outside_the_diff(findings: object, diff_lines: Mapping[str, frozenset[int]]) -> list[str]:
    """Return the citations that are missing a location or are not added lines in this diff."""
    if not isinstance(findings, list):
        return []
    outside: list[str] = []
    for item in cast(list[object], findings):
        if not isinstance(item, dict):
            outside.append(MISSING_CITATION)
            continue
        finding = cast(dict[str, object], item)
        path = finding.get("file")
        line = finding.get("line")
        if not isinstance(path, str) or isinstance(line, bool) or not isinstance(line, int):
            outside.append(MISSING_CITATION)
        elif line not in diff_lines.get(path, frozenset()):
            outside.append(f"{path}:{line}")
    return outside


def _shown(citations: Sequence[str]) -> str:
    """Name a few refused citations, counting the rest, never quoting finding text."""
    shown = ", ".join(citations[:MAX_REFUSED_CITATIONS])
    remaining = len(citations) - MAX_REFUSED_CITATIONS
    return f"{shown} and {remaining} more" if remaining > 0 else shown
