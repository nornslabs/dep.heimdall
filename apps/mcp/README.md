# MCP server

Exposes tenant-scoped tools to the Heimdall agents. `create_server(sessions, reader,
agent_name)` builds one MCP server per agent identity (`huginn`, `loki`, `heimdall`) and binds
its allowed tool set. Every tool runs inside `{installation_id, pr_id, head_sha}` scope taken
from the server session — the model never controls which PR's ref set is valid.

Allowed tools per agent:

|| Agent | Tools |
|---|---|---|
| `huginn` | `get_pr_context`, `get_diff`, `get_file`, `start_repro`, `get_repro`, `recall` |
| `loki` | `get_pr_context`, `get_diff`, `get_file`, `save_findings`, `recall` |
| `heimdall` | `get_pr_context`, `list_findings`, `get_repro`, `save_card`, `save_draft`, `draft_close`, `draft_reopen`, `draft_merge`, `draft_ask_contributor`, `rerun_checks`, `create_mechanical_task`, `recall` |

`get_file` (HEI-86) reads a line range of a file at the PR's head or base commit; its `ref` is
validated against the session's snapshot before any network call, and Heimdall's scope excludes
it. Memory tools (`recall`, `remember`, `forget`) are registered through
`apps.mcp._memory_tools`.

|| File | Role |
|---|---|---|
|| `main.py` | `create_server` plus per-agent tool handlers (`start_repro`, `get_repro`, memory tools) |
|| `_memory_tools.py` | `register_memory_tools`: read-only `recall` plus maintainer-gated `remember`/`forget` |
|| `_checks_tool.py` | `register_checks_tool`: `rerun_checks` asks triage to run all of a PR's checks again; triage decides whether it may |
