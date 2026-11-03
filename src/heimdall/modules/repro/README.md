# repro

Reproduction runs execute on GitHub Actions, store paired results for PR and base commits, and derive
a verdict. Attempts are tenant scoped, limited to three per head, and share the transaction that
queues execution and timeout jobs.

Public functions: `start_run`, `get_run`, `record_result`, `timeout_run`, `mark_runs_stale`, and
`derive_verdict`.

| File | Role |
|---|---|
| `api.py` | Public interface; the only file other modules import |
| `service.py` | Use case exports |
| `domain.py` | Pure statuses, values, verdict rules |
| `ports.py` | Adapter interfaces |
| `models.py` | Persistence model |
| `_runs.py` | Start attempts, retrieve state, record paired outcomes, and mark timeouts |
| `_stale.py` | Mark runs stale after a PR head changes |

Table `repro_runs` has a unique `(pr_id, head_sha, attempt)` key and stores `with_fix`,
`without_fix`, and `verdict`. `start_run` and `get_run` are exposed as MCP tools by `apps/mcp`;
run retrieval filters by installation id.
