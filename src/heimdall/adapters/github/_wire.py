"""The few fields Heimdall reads from GitHub's JSON, validated at the edge.

githubkit's full models need dozens of fields; these accept GitHub's replies and ignore the rest.
"""

from pydantic import BaseModel, ConfigDict, RootModel


class _Wire(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class Login(_Wire):
    login: str


class Label(_Wire):
    name: str


class GitRef(_Wire):
    sha: str


class PullRequestWire(_Wire):
    number: int
    title: str
    body: str | None = None
    state: str
    draft: bool = False
    user: Login
    head: GitRef
    base: GitRef
    labels: list[Label] = []
    assignees: list[Login] = []


class IssueWire(_Wire):
    """Issue fields needed by intake assignment checks and Loki's scope review."""

    number: int
    title: str = ""
    body: str | None = None
    assignees: list[Login] = []


class ChangedFileWire(_Wire):
    filename: str
    status: str
    additions: int
    deletions: int


class ChangedFilePageWire(RootModel[list[ChangedFileWire]]):
    """One page of a pull request's changed files (GitHub returns a bare JSON list)."""


class FileContentWire(_Wire):
    type: str
    encoding: str | None = None
    content: str | None = None


class SearchTotalWire(_Wire):
    total_count: int


class StatusWire(_Wire):
    state: str


class CombinedStatusWire(_Wire):
    statuses: list[StatusWire] = []


class CheckRunWire(_Wire):
    status: str
    conclusion: str | None = None


class CheckRunsWire(_Wire):
    check_runs: list[CheckRunWire] = []
