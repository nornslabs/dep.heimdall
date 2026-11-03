"""Interfaces intake needs from GitHub and configuration adapters."""

from typing import Protocol

from heimdall.modules.tenancy.api import ConfigFileReader
from heimdall.shared.github import CiState, PullRequestInfo, RepoRef


class IntakeReader(ConfigFileReader, Protocol):
    """GitHub reads intake needs, implemented by the GitHub adapter."""

    async def get_pull_request(self, repo: RepoRef, number: int) -> PullRequestInfo:
        """Load current PR author, body, labels and commit refs."""
        ...

    async def count_open_prs_by_author(self, repo: RepoRef, author: str) -> int:
        """Count open PRs authored by one contributor."""
        ...

    async def get_ci_state(self, repo: RepoRef, sha: str) -> CiState:
        """Return combined commit status and check-run state."""
        ...

    async def get_issue_assignees(self, repo: RepoRef, issue_number: int) -> tuple[str, ...]:
        """Return all assignee logins for the linked issue."""
        ...
