# adapters/github

Read-only GitHub access as a GitHub App installation. Only Gjallarhorn (`modules/approval`,
HEI-65) writes to GitHub; this adapter never does.

| File | Public names |
|---|---|
| `auth.py` | `load_private_key(path)`; `GitHubAppClients(app_id, private_key)` with `.for_installation(id)` |
| `reader.py` | `GitHubApiReader(clients)` and `MAX_DIFF_BYTES` |
| `_http.py`, `_wire.py`, `_errors.py`, `_ci.py` | Private: the typed GET, the response fields we read, error mapping, CI combining |

Value types live in `shared/github.py` (`RepoRef`, `PullRequestInfo`, `ChangedFile`, `PrDiff`,
`CiState`), so module ports can name them without importing this adapter.

## Reads

| Method | Returns |
|---|---|
| `get_file(repo, path, ref)` | File text at a commit, or `None` (missing file or directory) |
| `get_pull_request(repo, number)` | `PullRequestInfo` (`body` is `""` when empty) |
| `list_changed_files(repo, number)` | Every changed file, following pages (up to GitHub's 3000) |
| `get_diff(repo, number)` | Unified diff cut at 200 KB on a character boundary, with `truncated` |
| `count_open_prs_by_author(repo, author)` | One search call, using `total_count` |
| `get_ci_state(repo, sha)` | Statuses and check runs combined: any failure, then anything running, then success; `none` if nothing reported |

PR titles, bodies and diffs are untrusted data: returned as values, never logged.

## Auth

githubkit signs the app JWT and swaps it for an installation token on first use. Each
`GitHubAppClients` keeps its own token cache, and one client per installation. githubkit's
automatic retries are off, because our jobs and outbox already retry with backoff.

## Errors

| GitHub says | Raised |
|---|---|
| 5xx, timeout, network error, rate limit | `GitHubUnavailable` (retryable) |
| 404 on `get_file` | no error; returns `None` |
| Any other 4xx, or an unexpected reply | `GitHubRequestFailed` (not retryable) |

Messages carry the action and status only, never response bodies.

## Testing

githubkit 0.17 sends requests through `httpx2`, which `respx` cannot intercept, so tests pass
an `httpx2.MockTransport` through `GitHubAppClients(async_transport=...)`. See
`tests/integration/adapters/github/fake_github.py`. Tests generate a throwaway RSA key, so no
secret is committed, and they never touch the network.
