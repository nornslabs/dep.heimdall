"""The brief Huginn starts from: the PR, its changed tests, then the source they cover.

The order is guidance for the model, not a script: it still decides what to read and when to
run the reproduction. Only paths go in the brief; file content stays behind ``get_diff`` and
``get_file``, where the runtime wraps it as PR data.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass

from agents._guardrails import wrap_pr_data
from heimdall.adapters.ai import ChatMessage

MAX_BRIEF_FILES = 20
_TEST_DIRECTORIES = frozenset({"test", "tests"})
_TEST_PREFIX = "test_"
_TEST_SUFFIX = "_test"


@dataclass(frozen=True)
class ReproBrief:
    """What Huginn knows before its first tool call.

    Attributes:
        installation_id: Tenant whose tools Huginn may call.
        pr_id: Triage identifier every scoped tool takes.
        repo_full_name: ``owner/name`` of the repository under test.
        number: Pull request number.
        title: PR title; untrusted text from GitHub.
        body: PR body; untrusted text from GitHub.
        head_sha: Commit the reproduction runs against.
        test_cmd: Command from validated repository config, before Huginn narrows it.
        test_files: Changed test paths, read first.
        source_files: Changed non-test paths those tests cover.
    """

    installation_id: int
    pr_id: str
    repo_full_name: str
    number: int
    title: str
    body: str
    head_sha: str
    test_cmd: str
    test_files: tuple[str, ...]
    source_files: tuple[str, ...]


def build_repro_brief(
    *,
    installation_id: int,
    pr_id: str,
    repo_full_name: str,
    number: int,
    title: str,
    body: str,
    head_sha: str,
    test_cmd: str,
    changed_paths: Sequence[str],
) -> ReproBrief:
    """Split a PR's changed paths into the tests to read first and the source they cover.

    Args:
        installation_id: Tenant whose tools Huginn may call.
        pr_id: Triage identifier every scoped tool takes.
        repo_full_name: ``owner/name`` of the repository.
        number: Pull request number.
        title: PR title, kept as data.
        body: PR body, kept as data.
        head_sha: Commit under test.
        test_cmd: Command from validated repository config.
        changed_paths: Every path the PR touches, in GitHub's order.

    Returns:
        The brief, with both file lists capped at ``MAX_BRIEF_FILES``.
    """
    test_files = tuple(path for path in changed_paths if _is_test(path))
    source_files = _covering_sources(test_files, changed_paths)
    return ReproBrief(
        installation_id=installation_id,
        pr_id=pr_id,
        repo_full_name=repo_full_name,
        number=number,
        title=title,
        body=body,
        head_sha=head_sha,
        test_cmd=test_cmd,
        test_files=test_files[:MAX_BRIEF_FILES],
        source_files=source_files[:MAX_BRIEF_FILES],
    )


def build_messages(
    brief: ReproBrief, instructions: str, skill_index: str
) -> tuple[ChatMessage, ...]:
    """Build the system prompt and the single user turn that starts a Huginn run.

    Args:
        brief: The PR, its changed tests and the source they cover.
        instructions: Huginn's role prompt from ``prompt.md``.
        skill_index: The ``name: description`` list of skills, or an empty string.

    Returns:
        One system message and one user message holding the brief as PR data.
    """
    system = f"{instructions}\n\n{skill_index}" if skill_index else instructions
    return (ChatMessage("system", system), ChatMessage("user", _brief_message(brief)))


def _brief_message(brief: ReproBrief) -> str:
    """Render the brief as PR data, with the reading order stated outside the wrapper."""
    payload = {
        "repository": brief.repo_full_name,
        "pull_request": {
            "number": brief.number,
            "title": brief.title,
            "body": brief.body,
            "head_sha": brief.head_sha,
        },
        "configured_test_cmd": brief.test_cmd,
        "changed_test_files": list(brief.test_files),
        "source_they_cover": list(brief.source_files),
    }
    scope = f"installation_id {brief.installation_id} and pr_id {brief.pr_id}"
    plan = (
        f"Your tools are scoped to {scope}.\n"
        "Start from the brief below, then read the changed test files, then the source they\n"
        "cover. This order is guidance, not a script: read what you need and start the\n"
        "configured command when you are ready."
    )
    return f"{plan}\n\n{wrap_pr_data('pr_brief', json.dumps(payload, indent=2))}"


def _is_test(path: str) -> bool:
    """Say whether a path is a test file, by its name or a test directory."""
    parts = path.split("/")
    name = parts[-1]
    if name.startswith(_TEST_PREFIX) or _TEST_SUFFIX in name:
        return True
    return any(part in _TEST_DIRECTORIES for part in parts[:-1])


def _covering_sources(tests: Sequence[str], changed_paths: Sequence[str]) -> tuple[str, ...]:
    """Name the changed non-test files whose name matches a changed test's stem.

    A test named ``test_pager.py`` covers ``pager.py``. When nothing matches, Huginn gets
    every changed non-test path rather than an empty list.
    """
    sources = tuple(path for path in changed_paths if not _is_test(path))
    stems = tuple(stem for stem in (_stem(test) for test in tests) if stem)
    covered = tuple(path for path in sources if any(stem in _stem(path) for stem in stems))
    return covered or sources


def _stem(path: str) -> str:
    """Return a path's lower-case file name without test markers or extension."""
    name = path.rsplit("/", 1)[-1].casefold()
    if "." in name:
        name = name.rsplit(".", 1)[0]
    if name.endswith(_TEST_SUFFIX):
        return name[: -len(_TEST_SUFFIX)]
    if name.startswith(_TEST_PREFIX):
        return name[len(_TEST_PREFIX) :]
    return name
