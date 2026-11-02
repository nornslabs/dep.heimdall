# triage

The PR triage state machine.

| File | Role |
|---|---|
| `api.py` | Public interface (the only file other modules import) |
| `service.py` | Use cases |
| `domain.py` | Pure logic, no I/O |
| `ports.py` | Adapter interfaces |
| `models.py` | Persistence models |

Tables (`models.py`): `prs` (state, `state_version` for optimistic locking, unique `(repo_id, number)`), `events` (webhook deliveries keyed by `delivery_id`).
