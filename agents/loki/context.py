"""The brief Loki starts from: the PR, its changed tests, the source, then the linked issue.

The order is guidance for the model, not a script: it still decides what to read and when.
Only paths go in the brief; file content stays behind ``get_diff`` and ``get_file``, where the
runtime wraps it as PR data.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass

from agents._guardrails import wrap_pr_data
from agents.loki._notes import notes_block
from agents.loki.guardrails import is_docs_only
from heimdall.adapters.ai import ChatMessage

MAX_BRIEF_FILES = 40
"""Most paths listed for one group; the diff itself stays the source of detail."""

_TEST_DIRECTORIES = frozenset({"test", "tests"})
_TEST_PREFIX = "test_"
_TEST_SUFFIX = "_test"


@dataclass(frozen=True)
class LinkedIssue:
    """The issue a PR closes, used for the scope check. Title and body are untrusted text."""

    number: int
    title: str = ""
    body: str = ""


@dataclass(frozen=True)
class ReviewBrief:
    """What Loki knows before its first tool call.

    Attributes:
        installation_id: Tenant whose tools Loki may call.
        pr_id: Triage identifier every scoped tool takes.
        repo_full_name: ``owner/name`` of the repository under review.
        number: Pull request number.
        title: PR title; untrusted text from GitHub.
        body: PR body; untrusted text from GitHub.
        head_sha: Commit under review.
        base_sha: Base commit the diff is taken against.
        docs_only: True when every changed path matches ``review.skip_paths``.
        test_files: Changed test paths, read first.
        source_files: The remaining changed paths.
        linked_issue: The issue the PR closes, when intake found one.
    """

    installation_id: int
    pr_id: str
    repo_full_name: str
    number: int
    title: str
    body: str
    head_sha: str
    base_sha: str
    docs_only: bool
    test_files: tuple[str, ...]
    source_files: tuple[str, ...]
    linked_issue: LinkedIssue | None = None


def build_review_brief(
    *,
    installation_id: int,
    pr_id: str,
    repo_full_name: str,
    number: int,
    title: str,
    body: str,
    head_sha: str,
    base_sha: str,
    changed_paths: Sequence[str],
    skip_paths: Sequence[str],
    linked_issue: LinkedIssue | None = None,
) -> ReviewBrief:
    """Split a PR's changed paths into the tests to read first and the source they cover.

    Args:
        installation_id: Tenant whose tools Loki may call.
        pr_id: Triage identifier every scoped tool takes.
        repo_full_name: ``owner/name`` of the repository.
        number: Pull request number.
        title: PR title, kept as data.
        body: PR body, kept as data.
        head_sha: Commit under review.
        base_sha: Base commit the diff is taken against.
        changed_paths: Every path the PR touches, in GitHub's order.
        skip_paths: ``review.skip_paths`` from the repository's validated config.
        linked_issue: The issue the PR closes, when one was found.

    Returns:
        The brief, with both path lists capped at ``MAX_BRIEF_FILES``.
    """
    test_files = tuple(path for path in changed_paths if _is_test(path))
    source_files = tuple(path for path in changed_paths if not _is_test(path))
    return ReviewBrief(
        installation_id=installation_id,
        pr_id=pr_id,
        repo_full_name=repo_full_name,
        number=number,
        title=title,
        body=body,
        head_sha=head_sha,
        base_sha=base_sha,
        docs_only=is_docs_only(changed_paths, skip_paths),
        test_files=test_files[:MAX_BRIEF_FILES],
        source_files=source_files[:MAX_BRIEF_FILES],
        linked_issue=linked_issue,
    )


def build_messages(
    brief: ReviewBrief,
    instructions: str,
    skill_index: str,
    note_lines: Sequence[str] = (),
) -> tuple[ChatMessage, ...]:
    """Build the system prompt and the single user turn that starts a Loki run.

    Args:
        brief: The PR, its changed tests, the source they cover and the linked issue.
        instructions: Loki's role prompt from ``prompt.md``.
        skill_index: The ``name: description`` list of skills, or an empty string.
        note_lines: ``key: value`` notes maintainers kept for Loki on this repo.

    Returns:
        One system message and one user message holding the brief, the issue and any notes
        as PR data.
    """
    system = f"{instructions}\n\n{skill_index}" if skill_index else instructions
    return (ChatMessage("system", system), ChatMessage("user", _user_turn(brief, note_lines)))


def _user_turn(brief: ReviewBrief, note_lines: Sequence[str]) -> str:
    """Render the reading order, then the brief, the linked issue and any notes as data."""
    parts = [_brief_block(brief)]
    issue = _issue_block(brief.linked_issue)
    if issue:
        parts.append(issue)
    notes = notes_block(tuple(note_lines))
    if notes:
        parts.append(notes)
    return "\n\n".join(parts)


def _brief_block(brief: ReviewBrief) -> str:
    """Render the brief as PR data, with the reading order and docs-only rule stated first."""
    payload = {
        "repository": brief.repo_full_name,
        "pull_request": {
            "number": brief.number,
            "title": brief.title,
            "body": brief.body,
            "head_sha": brief.head_sha,
            "base_sha": brief.base_sha,
        },
        "docs_only": brief.docs_only,
        "changed_test_files": list(brief.test_files),
        "changed_source_files": list(brief.source_files),
    }
    plan = (
        f"Your tools are scoped to installation_id {brief.installation_id} and "
        f"pr_id {brief.pr_id}.\n"
        "Read in this order: the brief below, then the changed tests, then the source they\n"
        "cover, then the linked issue. This order is guidance, not a script: load a skill when\n"
        "the next step matches one, save findings as you confirm them, then finish."
    )
    if brief.docs_only:
        plan += (
            "\nThis PR only changes paths the review config skips: save no findings and submit\n"
            "skipped_reason 'docs_only'."
        )
    return f"{plan}\n\n{wrap_pr_data('pr_brief', json.dumps(payload, indent=2))}"


def _issue_block(issue: LinkedIssue | None) -> str:
    """Wrap the linked issue as PR data, or return "" when the PR has none."""
    if issue is None:
        return ""
    payload = {"number": issue.number, "title": issue.title, "body": issue.body}
    return wrap_pr_data("linked_issue", json.dumps(payload, indent=2))


def _is_test(path: str) -> bool:
    """Say whether a path is a test file, by its name or a test directory."""
    parts = path.split("/")
    name = parts[-1]
    if name.startswith(_TEST_PREFIX) or _TEST_SUFFIX in name:
        return True
    return any(part in _TEST_DIRECTORIES for part in parts[:-1])
