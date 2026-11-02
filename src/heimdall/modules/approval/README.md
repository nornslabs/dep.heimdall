# approval

Maintainer approvals and the Gjallarhorn GitHub outbox.

| File | Role |
|---|---|
| `api.py` | Public interface (the only file other modules import) |
| `service.py` | Use cases |
| `domain.py` | Pure logic, no I/O |
| `ports.py` | Adapter interfaces |
| `models.py` | Persistence models |

Tables (`models.py`): `approvals` (one per draft; `via` is `reaction` or `policy:auto_suggest`). The `outbox` table it sends from lives in `adapters/db/outbox.py` because every module writes to it..
