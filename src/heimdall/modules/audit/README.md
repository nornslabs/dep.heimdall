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

Public functions: none are exported yet; `api.py` remains empty until the audit use cases are
implemented. Both tables carry `installation_id` for tenant-scoped reporting.
