# PRD: HEI-86 — MCP tool `get_file(path, ref, start, end)`

| Field | Value |
| --- | --- |
| Issue | HEI-86 |
| Parent | HEI-83 — Make Huginn, Loki and Heimdall real tool-using agents |
| Blocks | HEI-91 (Huginn on agentic runtime), HEI-92 (Loki on agentic runtime) |
| Suggested branch | `priyankarpal/hei-86-3-mcp-tool-get_filepath-ref-start-end` |
| Status | Implemented |
| Design reference | `Heimdall agents agentic design.md`, step 3 |

## 1. Summary

Add a new read-only MCP tool `get_file(path, ref, start, end)` that lets Huginn and Loki read a
line range of a file at the PR's head or base commit through `GitHubApiReader.file_at_sha`. The
tool is scoped to the server session's `{installation_id, pr_id, head_sha}` — the agent never
controls which PR's ref set is valid. Heimdall's token receives `ToolNotAllowed`.

## 2. Problem

Today Huginn and Loki can only see the unified diff. When a changed line refers to context
outside the diff window — a function signature, a constant, an import — the agent cannot read it
without loading the full diff. A targeted line-range reader makes investigations cheaper and
narrows the context window.

## 3. Goals

- Agents can read a slice of a file (e.g. lines 40–80) at the PR's head or base commit.
- The session always controls which commits are valid; the model never escapes to an arbitrary ref.
- Heimdall cannot call `get_file` by design; its token's `allowed_tools` excludes it.
- The tool result is already wrapped and trimmed by the existing runtime context budget.
- A 400-line cap per call prevents oversized results before the budget trim.

## 4. Non-goals

- Any write to GitHub.
- Reading files outside the PR's head/base (arbitrary branch, tag, or SHA).
- Changing the approval, outbox, or other MCP tools.
- Adding `get_file` to Heimdall's tool set.
- Hot paths: the tool hits GitHub's contents API, which counts against rate limits like any other
  read call.

## 5. Design decisions

| Decision | Choice | Why |
| --- | --- | --- |
| New `GitHubApiReader` method | `file_at_sha(repo, path, sha)` | Reuses the existing reader + error layer; `get_file` already reads by ref but `file_at_sha` takes a validated SHA directly, keeping the public interface clean. |
| Ref validation | Server-side: compare `ref` arg against the snapshot's `head_sha` and `base_sha` | Scope comes from the server session, never from the model. |
| Line range | `start` (1-based, inclusive) and `end` (1-based, inclusive, optional) | Matches the convention in most editors and linters; 1-based is the natural human reference. |
| Line-range cap | 400 lines, in the server *and* the runtime budget trim | Open decision settled: yes. The cap keeps one result inside the smallest agent budget (24k tokens ≈ 96k chars; 400 × ~120 chars = ~48k chars) so the runtime trims only on top of an already small result. |
| File not found | Return `{"found": false, "reason": "not_found"}` | Not an error; the agent asked a valid question with a valid ref. |
| Binary / non-UTF-8 | Let the reader raise `GitHubRequestFailed`, which the runtime returns as `error: GitHubRequestFailed` | The adapter already refuses such content; reporting it as `not_found` would hide a real failure. |
| Heimdall access | Tool not registered in Heimdall's server; no runtime change needed | Enforced at the server level, consistent with existing scope guards. |
| `_mcp_tools.py` | Add `get_file` to `LokiMcpTools` and a `HuginnMcpTools` client | Symmetry with the other `McpToolClient` subclasses. |

## 6. Functional requirements

### FR-1 — `GitHubApiReader.file_at_sha`

Add a method:

```python
async def file_at_sha(self, repo: RepoRef, path: str, sha: str) -> str | None:
```

- Reads the file at exactly that SHA using the contents API (`?ref=<sha>`).
- Returns the decoded UTF-8 text, or `None` when the file does not exist or is not UTF-8.
- Raises `GitHubUnavailable` on rate limits / transient errors; `GitHubRequestFailed` on other
  API errors (same as `get_file`).
- The `path` is passed directly to GitHub; the server must not pre-sanitize it (GitHub handles
  path traversal at the API level).

### FR-2 — MCP tool `get_file`

Register on the Huginn and Loki MCP servers:

```
name:        get_file
description: Read a line range of a file at the PR head or base. Ref must be this PR's
             head_sha or base_sha. Lines are 1-based; omit end to read to EOF or the cap.
```

Input schema:

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `installation_id` | integer | yes | Tenant scope. |
| `pr_id` | string (UUID) | yes | PR scope. |
| `repo_full_name` | string | yes | Repository `owner/name`. |
| `path` | string | yes | File path inside the repository. |
| `ref` | string | yes | Must equal this PR's `head_sha` or `base_sha`. |
| `start` | integer | no | First line (1-based, inclusive). Default 1. |
| `end` | integer or null | no | Last line (1-based, inclusive). Omit to read up to the cap; must be at or after `start`. |

Output (on success):

```json
{
  "found": true,
  "path": "<path>",
  "ref": "<sha>",
  "start": 1,
  "end": 80,
  "lines": 80,
  "content": "<text>"
}
```

Output (file absent):

```json
{"found": false, "path": "<path>", "ref": "<sha>", "reason": "not_found"}
```

Output (backwards range):

```json
{"error": "end must be at or after start"}
```

### FR-3 — Ref scope guard

Before fetching from GitHub:

1. Load the PR snapshot from the database (`_get_pr_snapshot`).
2. Reject any `ref` that is neither `snapshot.head_sha` nor `snapshot.base_sha`:

```json
{"error": "ref is not this PR's head or base"}
```

3. Never log the `path` or the file content.

### FR-4 — Line range

- Clamp `start` up to at least 1.
- Clamp `end` to `min(end, start + LINE_CAP - 1)` where `LINE_CAP = 400`; omitted `end`
  means `start + LINE_CAP - 1`.
- A `start` past the last line returns no lines and reports the file's real last line as `end`,
  so a short file is never handed back as if it were the requested range.
- Return `start` and `end` in the response so the agent knows what it received.
- Lines are the text between `\n` characters; the last partial line counts.

### FR-5 — Agent scope

- Huginn `allowed_tools`: add `"get_file"`.
- Loki `allowed_tools`: add `"get_file"`.
- Heimdall `allowed_tools`: unchanged — `"get_file"` is not added.
- Heimdall's `create_server` call must not register `get_file`.
- The existing `allowed_tools` enforcement in `create_server` already removes any tool not in the
  agent's allow set, so no extra guard is needed.

### FR-6 — `_mcp_tools.py` clients

Add `get_file(...)` to `LokiMcpTools`, delegating to `self.call("get_file", ...)` and returning
`dict[str, object]`. Huginn keeps its `_DatabaseHuginnTools` adapter until HEI-91 moves it onto
the shared runtime; its client is added there, beside its other tools.

### FR-7 — Budget integration

The runtime wraps every tool result as `pr_data` and trims it. No change to the budget layer is
needed; the 400-line cap ensures the raw content is small enough to fit before trimming starts.

## 7. Security

- **Model-provided `path`** is untrusted. It is forwarded to GitHub's contents API as-is;
  GitHub enforces its own path rules. The server must not concatenate it with `os.path`.
- **Model-provided `ref`** is validated against the server's session-scoped snapshot before
  any network call. The agent cannot read an arbitrary commit.
- **`installation_id` and `pr_id`** come from the model's arguments (as with all existing tools)
  but are verified against the stored snapshot, so tenant-crossing is not possible.
- **File content** is untrusted data returned to the model. It must never be logged.
- **`path` and `ref`** are not logged (names only, not values, consistent with AGENTS.md security
  rules).

## 8. Observability

No new log events. The tool call is visible through the existing MCP tool tracing.

## 9. Acceptance criteria

- [x] **AC-1** Huginn and Loki MCP servers expose `get_file` in their tool list.
- [x] **AC-2** Heimdall's MCP server does not expose `get_file`.
- [x] **AC-3** `get_file` with the PR's `head_sha` returns the correct line range.
- [x] **AC-4** `get_file` with the PR's `base_sha` returns the correct line range.
- [x] **AC-5** `get_file` with a SHA that is neither `head_sha` nor `base_sha` returns
      `{"error": "ref is not this PR's head or base"}`.
- [x] **AC-6** `get_file` for a non-existent path returns `{"found": false, ..., "reason": "not_found"}`.
- [x] **AC-7** `get_file` with `end - start >= 400` is clamped to 400 lines.
- [x] **AC-8** `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright` and
      `uv run pytest tests/unit` pass.
- [x] **AC-9** No new dependency is added.

## 10. Test plan

### Unit — `tests/unit/apps/test_get_file_tool.py`

All tests use a fake `GitHubApiReader` and a fake `_get_pr_snapshot`. No DB, no network.

- **Huginn server exposes `get_file`.**
- **Loki server exposes `get_file`.**
- **Heimdall server does not expose `get_file`.**
- **Valid `head_sha` ref returns file content at correct line range.**
- **Valid `base_sha` ref returns file content at correct line range.**
- **Out-of-scope `ref` returns error dict without calling GitHub.**
- **File not found returns `{"found": false, "reason": "not_found"}`.**
- **Line range clamped at 400 lines.**
- **`start` defaults to 1 when omitted.**
- **`end` defaults to `start + 399` when omitted.**
- **`start` beyond EOF returns empty content with clamped range.**

### Unit — `tests/unit/apps/test_mcp_server.py` (update)

Update the parametrised `test_each_agent_mcp_contract_is_tenant_scoped` to include `get_file` in
Huginn's and Loki's expected tool sets.

### Unit — `tests/unit/apps/test_loki_tool_scope.py` (update)

Assert `"get_file"` in `ALLOWED_TOOLS`.

### Repository invariant — `tests/unit/adapters/github/test_reader.py` (add)

`file_at_sha` is covered by the existing mock-transport test pattern used in that file.

## 11. Out of scope for follow-up

- Directory listing via `get_file` (GitHub returns a list; the tool returns `not_found`).
- Streaming large files in chunks.
- `get_thread`, `recall`, `remember`, `forget`, `agent_memory` (HEI-part-B).
