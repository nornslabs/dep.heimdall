"""Interfaces the tenancy module needs from adapters."""

from typing import Protocol

from heimdall.shared.github import RepoRef


class ConfigFileReader(Protocol):
    """Reads heimdall.yml files at a commit (implemented by the GitHub adapter)."""

    async def get_file(self, repo: RepoRef, path: str, ref: str) -> str | None:
        """Return the file's text at ``ref``, or ``None`` when there is no such file."""
        ...
