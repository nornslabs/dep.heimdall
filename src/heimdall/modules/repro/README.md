# repro

Reproduction runs execute on GitHub Actions, store paired results for PR and base commits, and
derive a verdict. Attempts are tenant scoped and limited to three per head. Starting a run
transactionally queues execution and timeout jobs plus an exact-payload authorized workflow
dispatch through the approval module.

Triage hears that reproduction is done only when the last attempt for a head has finished or
timed out. An earlier attempt ending while others still run reports nothing, so Heimdall's card
is never built against an attempt that has no verdict yet.

Public functions: `start_run`, `get_run`, `latest_run_id`, `record_result`, `timeout_run`, `mark_runs_stale`, and
`derive_verdict`.

| File | Role |
|---|---|
| `api.py` | Public interface; only file other modules import |
| `service.py` | Use case exports |
| `domain.py` | Pure statuses, values, and verdict rules |
| `models.py` | Reproduction persistence model |
| `_runs.py` | Start attempts, retrieve state, record paired outcomes, and mark timeouts |
| `_stale.py` | Mark runs stale after a PR head changes |

Table `repro_runs` stores run state, paired outcomes, and verdict. `workflow_dispatch_approvals`
stores dispatch authorization and is owned by approval.

## Related documentation

- `docs/how-it-works.md` — Huginn reproduction (stage 4).
- `docs/config.md` — the `repro` section of `heimdall.yml`.
- `docs/security.md` — read-only repro workflow with no secrets.
