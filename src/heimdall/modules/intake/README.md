# intake

The module stores one result per rule and pull request head. A synchronization event calls
`mark_results_stale` through the public API in the same transaction as the new PR head.

The Gate: plain-code intake rule checks.

| File | Role |
|---|---|
| `api.py` | Public interface (the only file other modules import) |
| `service.py` | Use cases |
| `domain.py` | Pure logic, no I/O |
| `ports.py` | Adapter interfaces |
| `models.py` | Persistence models |

Tables (`models.py`): `intake_results` (one row per rule per head commit).

`api.py` exports `IntakeFacts`, `RuleResult`, `RuleName`, `evaluate_rules(facts, config)`,
`run_intake(...)`, and `mark_results_stale(...)`. `run_intake` fetches GitHub facts, checks the
head-scoped config, upserts six rule outcomes, then sends the checklist to Switch through the
outbox and advances triage in the same transaction. CLA status comes from PR labels
`cla-signed`, `cla: yes`, or `cla: signed` when the repo requires a CLA.

The six checks are linked issue, author assignment, required labels, open PR limit, CLA, and CI.
