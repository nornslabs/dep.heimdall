# audit

Urd's Well stores append-only audit records and model token usage per tenant.

| File | Role |
|---|---|
| `api.py` | Public interface imported by other modules |
| `service.py` | Tenant-scoped write use cases |
| `domain.py` | Pure audit rules |
| `ports.py` | Adapter interfaces |
| `models.py` | Persistence models |

Tables (`models.py`): `audit_log` is insert-only; `usage` stores model token counts.

Public functions: `record_usage(...)` saves one agent's input and output token
counts for tenant-scoped reporting.
