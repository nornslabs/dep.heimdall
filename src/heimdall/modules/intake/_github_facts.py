"""Collect the GitHub facts used by the pure intake rules."""

import asyncio
import re

from heimdall.modules.intake.domain import IntakeFacts
from heimdall.modules.intake.ports import IntakeReader
from heimdall.modules.tenancy.api import IntakeConfig
from heimdall.shared.github import CiState, PullRequestInfo, RepoRef

LINKED_ISSUE_PATTERN = re.compile(r"\bcloses\s+#(\d+)\b", re.I)
SIGNED_CLA_LABELS = frozenset({"cla-signed", "cla: yes", "cla: signed"})


def linked_issue_number(body: str) -> int | None:
    """Extract the first closing issue reference from the PR body.

    Args:
        body: Untrusted pull request description.

    Returns:
        Linked issue number, or None when there is no supported reference.
    """
    match = LINKED_ISSUE_PATTERN.search(body)
    return int(match.group(1)) if match else None


async def collect_intake_facts(
    reader: IntakeReader,
    repo: RepoRef,
    pull_request: PullRequestInfo,
    config: IntakeConfig,
) -> IntakeFacts:
    """Fetch GitHub facts required to evaluate a pull request.

    Args:
        reader: GitHub reader for PR, issue, CI and author counts.
        repo: Tenant repository under evaluation.
        pull_request: Latest GitHub PR data.
        config: Effective intake configuration.

    Returns:
        Facts consumed by the pure intake rules.
    """
    issue_number = linked_issue_number(pull_request.body)
    open_prs, ci_state, assignees = await asyncio.gather(
        reader.count_open_prs_by_author(repo, pull_request.author),
        _fetch_ci_state(reader, repo, pull_request.head_sha, config.require_ci_green),
        reader.get_issue_assignees(repo, issue_number)
        if issue_number is not None and config.require_assignee
        else _no_assignees(),
    )
    cla_signed = any(label.casefold() in SIGNED_CLA_LABELS for label in pull_request.labels)
    return IntakeFacts(
        author=pull_request.author,
        body=pull_request.body,
        labels=pull_request.labels,
        open_prs_by_author=open_prs,
        issue_assignee=(
            pull_request.author
            if any(assignee.casefold() == pull_request.author.casefold() for assignee in assignees)
            else None
        ),
        cla_signed=cla_signed,
        ci_green=ci_state is CiState.SUCCESS,
    )


async def _no_assignees() -> tuple[str, ...]:
    """Match the issue lookup shape when no linked issue is required."""
    return ()


async def _fetch_ci_state(
    reader: IntakeReader,
    repo: RepoRef,
    head_sha: str,
    required: bool,
) -> CiState:
    """Skip a GitHub status request when config disables the CI rule."""
    if not required:
        return CiState.SUCCESS
    return await reader.get_ci_state(repo, head_sha)
