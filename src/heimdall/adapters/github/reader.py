"""Read-only GitHub access for Heimdall: files, pull requests, diffs, CI state.

Only Gjallarhorn (``approval``) writes to GitHub; this adapter never does.
"""

import base64
import binascii

from githubkit.exception import RequestFailed

from heimdall.adapters.github import _wire
from heimdall.adapters.github._ci import combine_ci
from heimdall.adapters.github._errors import github_errors, is_not_found
from heimdall.adapters.github._http import get
from heimdall.adapters.github.auth import GitHubAppClients
from heimdall.shared.errors import GitHubRequestFailed
from heimdall.shared.github import (
    ChangedFile,
    CiState,
    IssueInfo,
    PrDiff,
    PullRequestInfo,
    RepoRef,
)

MAX_DIFF_BYTES = 200_000
"""The LLD's cap on the diff handed to agents (get_diff returns at most 200 KB)."""

PAGE_SIZE = 100
MAX_FILE_PAGES = 30
"""GitHub lists at most 3000 files per pull request: 30 pages of 100."""

DIFF_MEDIA_TYPE = "application/vnd.github.diff"
FILE_TYPE = "file"
BASE64 = "base64"


class GitHubApiReader:
    """Reads repository and pull request data as the repo's installation."""

    def __init__(self, clients: GitHubAppClients) -> None:
        """Use ``clients`` to get an authenticated client per installation."""
        self._clients = clients

    async def get_file(self, repo: RepoRef, path: str, ref: str | None) -> str | None:
        """Read a text file at a commit, such as heimdall.yml at the PR's head.

        Args:
            repo: The repository and its installation.
            path: File path inside the repository.
            ref: Commit sha (or branch) to read at; ``None`` reads the default branch.

        Returns:
            The file's text, or ``None`` if there is no file at that path.

        Raises:
            GitHubUnavailable: GitHub is down, slow or rate limiting (retryable).
            GitHubRequestFailed: any other refusal, or content that is not base64 text.
        """
        return await self._contents(repo, path, ref, "get file")

    async def file_at_sha(self, repo: RepoRef, path: str, sha: str) -> str | None:
        """Read a text file at an exact commit SHA.

        Unlike ``get_file``, this always passes a ref so it never falls back to the default
        branch, and accepts only a full or abbreviated SHA (no branch names).

        Args:
            repo: The repository and its installation.
            path: File path inside the repository.
            sha: The exact commit SHA to read the file at.

        Returns:
            The file's decoded UTF-8 text, or ``None`` when there is no file at that path
            (missing, a directory, or content GitHub does not inline).

        Raises:
            GitHubUnavailable: GitHub is down, slow or rate limiting (retryable).
            GitHubRequestFailed: any other refusal, or content that is not base64 text.
        """
        return await self._contents(repo, path, sha, "file at sha")

    async def get_pull_request(self, repo: RepoRef, number: int) -> PullRequestInfo:
        """Read a pull request's details.

        Args:
            repo: The repository and its installation.
            number: The pull request number.

        Returns:
            The pull request; ``body`` is ``""`` when GitHub has none.

        Raises:
            GitHubUnavailable: GitHub is down, slow or rate limiting (retryable).
            GitHubRequestFailed: the pull request does not exist or the reply is malformed.
        """
        async with github_errors("get pull request"):
            reply = await get(self._client(repo), _pr_url(repo, number))
            return _to_pull_request(_wire.PullRequestWire.model_validate(reply.payload))

    async def list_changed_files(self, repo: RepoRef, number: int) -> tuple[ChangedFile, ...]:
        """List every file a pull request changes, following GitHub's pages.

        Args:
            repo: The repository and its installation.
            number: The pull request number.

        Returns:
            The changed files in GitHub's order.

        Raises:
            GitHubUnavailable: GitHub is down, slow or rate limiting (retryable).
            GitHubRequestFailed: the pull request does not exist or the reply is malformed.
        """
        files: list[ChangedFile] = []
        async with github_errors("list changed files"):
            for page in range(1, MAX_FILE_PAGES + 1):
                batch = await self._file_page(repo, number, page)
                files += [_to_changed_file(item) for item in batch]
                if len(batch) < PAGE_SIZE:
                    break
        return tuple(files)

    async def get_diff(self, repo: RepoRef, number: int) -> PrDiff:
        """Read a pull request's unified diff, cut at ``MAX_DIFF_BYTES``.

        Args:
            repo: The repository and its installation.
            number: The pull request number.

        Returns:
            The diff text (untrusted; never log it) and whether it was cut short.

        Raises:
            GitHubUnavailable: GitHub is down, slow or rate limiting (retryable).
            GitHubRequestFailed: the pull request does not exist.
        """
        async with github_errors("get diff"):
            reply = await get(self._client(repo), _pr_url(repo, number), accept=DIFF_MEDIA_TYPE)
        return _cap_diff(reply.text)

    async def count_open_prs_by_author(self, repo: RepoRef, author: str) -> int:
        """Count an author's open pull requests in the repository, for the intake limit.

        Args:
            repo: The repository and its installation.
            author: GitHub login.

        Returns:
            The number of open pull requests by ``author``.

        Raises:
            GitHubUnavailable: GitHub is down, slow or rate limiting (retryable).
            GitHubRequestFailed: GitHub refused the search.
        """
        query = f"repo:{repo.full_name} is:pr is:open author:{author}"
        async with github_errors("count open pull requests"):
            reply = await get(
                self._client(repo), "/search/issues", params={"q": query, "per_page": 1}
            )
            return _wire.SearchTotalWire.model_validate(reply.payload).total_count

    async def get_issue(self, repo: RepoRef, issue_number: int) -> IssueInfo:
        """Read a linked issue's title and body, so a review can check the PR's scope.

        Args:
            repo: Repository and installation that owns the issue.
            issue_number: Linked issue number.

        Returns:
            The issue; ``body`` is ``""`` when GitHub has none.

        Raises:
            GitHubUnavailable: GitHub is down, slow or rate limiting (retryable).
            GitHubRequestFailed: the issue does not exist or the reply is malformed.
        """
        async with github_errors("get issue"):
            reply = await get(self._client(repo), f"/repos/{repo.full_name}/issues/{issue_number}")
            issue = _wire.IssueWire.model_validate(reply.payload)
        return IssueInfo(number=issue.number, title=issue.title, body=issue.body or "")

    async def get_issue_assignees(self, repo: RepoRef, issue_number: int) -> tuple[str, ...]:
        """Read all assignees of a linked issue.

        Args:
            repo: Repository and installation that owns the issue.
            issue_number: Linked issue number.

        Returns:
            Assignee logins, or an empty tuple when the issue has none.
        """
        async with github_errors("get issue assignee"):
            reply = await get(self._client(repo), f"/repos/{repo.full_name}/issues/{issue_number}")
        issue = _wire.IssueWire.model_validate(reply.payload)
        return tuple(assignee.login for assignee in issue.assignees)

    async def get_ci_state(self, repo: RepoRef, sha: str) -> CiState:
        """Combine a commit's statuses and check runs into one CI state.

        Args:
            repo: The repository and its installation.
            sha: The commit to check, usually the PR's head.

        Returns:
            The combined state; ``NONE`` when no CI reported on the commit.

        Raises:
            GitHubUnavailable: GitHub is down, slow or rate limiting (retryable).
            GitHubRequestFailed: GitHub refused the request or the reply is malformed.
        """
        base = f"/repos/{repo.full_name}/commits/{sha}"
        client = self._client(repo)
        async with github_errors("get CI state"):
            status = await get(client, f"{base}/status")
            checks = await get(client, f"{base}/check-runs", params={"per_page": PAGE_SIZE})
            statuses = _wire.CombinedStatusWire.model_validate(status.payload).statuses
            runs = _wire.CheckRunsWire.model_validate(checks.payload).check_runs
        return combine_ci(
            [item.state for item in statuses], [(run.status, run.conclusion) for run in runs]
        )

    async def _contents(self, repo: RepoRef, path: str, ref: str | None, action: str) -> str | None:
        """Read one file through the contents API, mapping an absent file to ``None``.

        Args:
            repo: The repository and its installation.
            path: File path inside the repository.
            ref: Commit sha or branch; ``None`` reads the default branch.
            action: Name of the read, used in error messages only.

        Returns:
            The file's text, or ``None`` when the path is not an inline file.
        """
        async with github_errors(action):
            try:
                params = {"ref": ref} if ref is not None else None
                reply = await get(
                    self._client(repo), f"/repos/{repo.full_name}/contents/{path}", params=params
                )
            except RequestFailed as error:
                if is_not_found(error):
                    return None
                raise
            if isinstance(reply.payload, list):
                return None  # a directory listing, not a file
            return _decode_file(_wire.FileContentWire.model_validate(reply.payload))

    def _client(self, repo: RepoRef):
        return self._clients.for_installation(repo.installation_id)

    async def _file_page(
        self, repo: RepoRef, number: int, page: int
    ) -> list[_wire.ChangedFileWire]:
        reply = await get(
            self._client(repo),
            f"{_pr_url(repo, number)}/files",
            params={"per_page": PAGE_SIZE, "page": page},
        )
        return _wire.ChangedFilePageWire.model_validate(reply.payload).root


def _pr_url(repo: RepoRef, number: int) -> str:
    return f"/repos/{repo.full_name}/pulls/{number}"


def _decode_file(content: _wire.FileContentWire) -> str | None:
    """Decode a contents-API file; directories, symlinks and submodules give ``None``."""
    if content.type != FILE_TYPE or content.encoding != BASE64 or content.content is None:
        return None
    try:
        return base64.b64decode(content.content).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        raise GitHubRequestFailed("file is not UTF-8 text") from None


def _to_pull_request(wire: _wire.PullRequestWire) -> PullRequestInfo:
    return PullRequestInfo(
        number=wire.number,
        title=wire.title,
        body=wire.body or "",
        author=wire.user.login,
        head_sha=wire.head.sha,
        base_sha=wire.base.sha,
        labels=tuple(label.name for label in wire.labels),
        assignees=tuple(assignee.login for assignee in wire.assignees),
        state=wire.state,
        draft=wire.draft,
    )


def _to_changed_file(wire: _wire.ChangedFileWire) -> ChangedFile:
    return ChangedFile(
        path=wire.filename, status=wire.status, additions=wire.additions, deletions=wire.deletions
    )


def _cap_diff(text: str) -> PrDiff:
    """Cut the diff to ``MAX_DIFF_BYTES`` without splitting a UTF-8 character."""
    encoded = text.encode("utf-8")
    if len(encoded) <= MAX_DIFF_BYTES:
        return PrDiff(text=text, truncated=False)
    cut = encoded[:MAX_DIFF_BYTES].decode("utf-8", errors="ignore")
    return PrDiff(text=cut, truncated=True)
