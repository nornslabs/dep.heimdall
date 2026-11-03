# audit

Urd's Well: the append-only audit log.

| File | Role |
|---|---|
| `api.py` | Public interface (the only file other modules import) |
| `service.py` | Use cases |
| `domain.py` | Pure logic, no I/O |
| `ports.py` | Adapter interfaces |
| `models.py` | Persistence models |

Tables (`models.py`): `audit_log` (insert-only: a trigger rejects UPDATE, DELETE and TRUNCATE), `usage` (agent token counts).

Public functions (`api.py`):

- `record_usage(sessions, ctx, UsageRecord)` saves one `usage` row with the tokens an agent run
  spent and the model string the provider reported. Agents call it once per run.
- `UsageRecord` is the frozen value it takes. It rejects a blank agent or model and negative counts.

Both tables carry `installation_id` for tenant-scoped reporting.
