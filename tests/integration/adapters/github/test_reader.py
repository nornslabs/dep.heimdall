import httpx2
import pytest

from heimdall.adapters.github.reader import MAX_DIFF_BYTES, GitHubApiReader
from heimdall.shared.errors import GitHubRequestFailed, GitHubUnavailable
from heimdall.shared.github import ChangedFile, CiState, PullRequestInfo, RepoRef
from heimdall.shared.ids import InstallationId
from tests.integration.adapters.github.fake_github import FakeGitHub, file_content, json_response

REPO = RepoRef(installation_id=InstallationId(11), full_name="octo-org/demo")
PR_PATH = "/repos/octo-org/demo/pulls/42"

PR_JSON = {
    "number": 42,
    "title": "Fix the frobnicator",
    "body": "Fixes #7",
    "state": "open",
    "draft": False,
    "user": {"login": "ravi"},
    "head": {"sha": "head123", "ref": "fix"},
    "base": {"sha": "base456", "ref": "main"},
    "labels": [{"name": "hackathon"}, {"name": "bug"}],
    "assignees": [{"login": "meera"}],
}


# --- get_file -------------------------------------------------------------------------


async def test_get_file_decodes_content_at_the_given_ref(
    reader: GitHubApiReader, fake_github: FakeGitHub
) -> None:
    path = "/repos/octo-org/demo/contents/.github/heimdall.yml"
    fake_github.on_json("GET", path, file_content("intake: {}\n", ".github/heimdall.yml"))

    text = await reader.get_file(REPO, ".github/heimdall.yml", "head123")

    assert text == "intake: {}\n"
    assert fake_github.requests_to(path)[0].url.params["ref"] == "head123"


async def test_missing_file_returns_none(reader: GitHubApiReader) -> None:
    assert await reader.get_file(REPO, "heimdall.yml", "head123") is None


async def test_directory_returns_none(reader: GitHubApiReader, fake_github: FakeGitHub) -> None:
    fake_github.on_json("GET", "/repos/octo-org/demo/contents/src", [{"type": "file"}])

    assert await reader.get_file(REPO, "src", "head123") is None


# --- pull requests --------------------------------------------------------------------


async def test_get_pull_request_maps_fields(
    reader: GitHubApiReader, fake_github: FakeGitHub
) -> None:
    fake_github.on_json("GET", PR_PATH, PR_JSON)

    assert await reader.get_pull_request(REPO, 42) == PullRequestInfo(
        number=42,
        title="Fix the frobnicator",
        body="Fixes #7",
        author="ravi",
        head_sha="head123",
        base_sha="base456",
        labels=("hackathon", "bug"),
        assignees=("meera",),
        state="open",
        draft=False,
    )


async def test_pull_request_without_body_has_empty_body(
    reader: GitHubApiReader, fake_github: FakeGitHub
) -> None:
    fake_github.on_json("GET", PR_PATH, {**PR_JSON, "body": None})

    assert (await reader.get_pull_request(REPO, 42)).body == ""


def _file_page(start: int, count: int) -> list[dict[str, object]]:
    return [
        {"filename": f"src/f{i}.py", "status": "modified", "additions": i, "deletions": 1}
        for i in range(start, start + count)
    ]


async def test_list_changed_files_follows_pagination(
    reader: GitHubApiReader, fake_github: FakeGitHub
) -> None:
    def pages(request: httpx2.Request) -> httpx2.Response:
        page = int(request.url.params.get("page", "1"))
        return json_response(200, _file_page(0, 100) if page == 1 else _file_page(100, 3))

    fake_github.on("GET", f"{PR_PATH}/files", pages)

    files = await reader.list_changed_files(REPO, 42)

    assert len(files) == 103
    assert files[0] == ChangedFile(path="src/f0.py", status="modified", additions=0, deletions=1)
    assert files[-1].path == "src/f102.py"


async def test_get_diff_asks_for_the_diff_format(
    reader: GitHubApiReader, fake_github: FakeGitHub
) -> None:
    fake_github.on("GET", PR_PATH, lambda _r: httpx2.Response(200, text="diff --git a b\n"))

    diff = await reader.get_diff(REPO, 42)

    assert diff.text == "diff --git a b\n"
    assert diff.truncated is False
    assert fake_github.requests_to(PR_PATH)[0].headers["Accept"] == "application/vnd.github.diff"


async def test_get_diff_is_cut_at_200kb_on_a_character_boundary(
    reader: GitHubApiReader, fake_github: FakeGitHub
) -> None:
    big = "é" * MAX_DIFF_BYTES  # 2 bytes each, so the cut lands mid-character
    fake_github.on("GET", PR_PATH, lambda _r: httpx2.Response(200, text=big))

    diff = await reader.get_diff(REPO, 42)

    assert diff.truncated is True
    assert len(diff.text.encode()) <= MAX_DIFF_BYTES
    assert diff.text == "é" * (MAX_DIFF_BYTES // 2)


async def test_count_open_prs_by_author_uses_search_total(
    reader: GitHubApiReader, fake_github: FakeGitHub
) -> None:
    fake_github.on_json("GET", "/search/issues", {"total_count": 4, "items": []})

    assert await reader.count_open_prs_by_author(REPO, "ravi") == 4
    query = fake_github.requests_to("/search/issues")[0].url.params["q"]
    assert query == "repo:octo-org/demo is:pr is:open author:ravi"


# --- CI state -------------------------------------------------------------------------


def _ci(fake_github: FakeGitHub, statuses: list[str], runs: list[tuple[str, str | None]]) -> None:
    base = "/repos/octo-org/demo/commits/head123"
    fake_github.on_json(
        "GET", f"{base}/status", {"statuses": [{"state": state} for state in statuses]}
    )
    fake_github.on_json(
        "GET",
        f"{base}/check-runs",
        {
            "total_count": len(runs),
            "check_runs": [{"status": s, "conclusion": c} for s, c in runs],
        },
    )


@pytest.mark.parametrize(
    ("statuses", "runs", "expected"),
    [
        (["success"], [("completed", "failure")], CiState.FAILURE),
        (["failure"], [("completed", "success")], CiState.FAILURE),
        (["error"], [], CiState.FAILURE),
        (["success"], [("in_progress", None)], CiState.PENDING),
        (["pending"], [("completed", "success")], CiState.PENDING),
        (["success"], [("completed", "success"), ("completed", "skipped")], CiState.SUCCESS),
        ([], [("completed", "neutral")], CiState.SUCCESS),
        ([], [], CiState.NONE),
    ],
)
async def test_ci_state_combines_statuses_and_check_runs(
    reader: GitHubApiReader,
    fake_github: FakeGitHub,
    statuses: list[str],
    runs: list[tuple[str, str | None]],
    expected: CiState,
) -> None:
    _ci(fake_github, statuses, runs)

    assert await reader.get_ci_state(REPO, "head123") is expected


# --- errors ---------------------------------------------------------------------------


async def test_server_error_raises_retryable_github_unavailable(
    reader: GitHubApiReader, fake_github: FakeGitHub
) -> None:
    fake_github.on_json("GET", PR_PATH, {"message": "boom"}, status=502)

    with pytest.raises(GitHubUnavailable) as raised:
        await reader.get_pull_request(REPO, 42)

    assert raised.value.retryable is True


async def test_rate_limit_raises_retryable_github_unavailable(
    reader: GitHubApiReader, fake_github: FakeGitHub
) -> None:
    headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "4102444800"}
    fake_github.on(
        "GET", PR_PATH, lambda _r: json_response(403, {"message": "rate limit"}, headers)
    )

    with pytest.raises(GitHubUnavailable):
        await reader.get_pull_request(REPO, 42)


async def test_timeout_raises_retryable_github_unavailable(
    reader: GitHubApiReader, fake_github: FakeGitHub
) -> None:
    def time_out(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    fake_github.on("GET", PR_PATH, time_out)

    with pytest.raises(GitHubUnavailable):
        await reader.get_pull_request(REPO, 42)


async def test_not_found_pr_raises_non_retryable_error(reader: GitHubApiReader) -> None:
    with pytest.raises(GitHubRequestFailed) as raised:
        await reader.get_pull_request(REPO, 999)

    assert raised.value.retryable is False
    assert "404" in str(raised.value)


async def test_malformed_response_raises_non_retryable_error(
    reader: GitHubApiReader, fake_github: FakeGitHub
) -> None:
    fake_github.on_json("GET", PR_PATH, {"number": "not-a-number"})

    with pytest.raises(GitHubRequestFailed):
        await reader.get_pull_request(REPO, 42)


async def test_get_file_without_ref_reads_the_default_branch(
    reader: GitHubApiReader, fake_github: FakeGitHub
) -> None:
    path = "/repos/octo-org/demo/contents/heimdall.yml"
    fake_github.on_json("GET", path, file_content("version: 1\n"))

    assert await reader.get_file(REPO, "heimdall.yml", None) == "version: 1\n"
    assert "ref" not in fake_github.requests_to(path)[0].url.params
