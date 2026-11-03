"""Interfaces review needs from GitHub readers."""

from typing import Protocol

from heimdall.shared.github import ChangedFile, RepoRef


class ReviewReader(Protocol):
    """Reads the current PR diff file list for citation validation."""

    async def list_changed_files(self, repo: RepoRef, number: int) -> tuple[ChangedFile, ...]:
        """Return changed paths at the PR head."""
        ...
