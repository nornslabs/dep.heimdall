"""GitHub read values that cross module borders, so ports never import an adapter."""

from dataclasses import dataclass
from enum import StrEnum

from heimdall.shared.ids import InstallationId


@dataclass(frozen=True)
class RepoRef:
    """A repository and the installation whose token may read it.

    Attributes:
        installation_id: The GitHub App installation (the tenant).
        full_name: ``owner/name``, for example ``octo-org/demo``.
    """

    installation_id: InstallationId
    full_name: str

    @property
    def owner(self) -> str:
        """The account that owns the repository."""
        return self.full_name.split("/", 1)[0]

    @property
    def name(self) -> str:
        """The repository name without its owner."""
        return self.full_name.split("/", 1)[1]


@dataclass(frozen=True)
class PullRequestInfo:
    """What Heimdall reads about a pull request. ``title`` and ``body`` are untrusted text."""

    number: int
    title: str
    body: str
    author: str
    head_sha: str
    base_sha: str
    labels: tuple[str, ...]
    assignees: tuple[str, ...]
    state: str
    draft: bool


@dataclass(frozen=True)
class IssueInfo:
    """What Heimdall reads about an issue. ``title`` and ``body`` are untrusted text."""

    number: int
    title: str
    body: str


@dataclass(frozen=True)
class ChangedFile:
    """One file a pull request touches, with its line counts."""

    path: str
    status: str
    additions: int
    deletions: int


@dataclass(frozen=True)
class PrDiff:
    """A pull request's unified diff, possibly cut short. Untrusted text; never log it."""

    text: str
    truncated: bool


class CiState(StrEnum):
    """The combined result of a commit's statuses and check runs."""

    SUCCESS = "success"
    PENDING = "pending"
    FAILURE = "failure"
    NONE = "none"
