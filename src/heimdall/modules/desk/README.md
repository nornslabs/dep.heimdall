# desk

Cards, drafts and the maintainer command parser.

| File | Role |
|---|---|
| `api.py` | Public interface (the only file other modules import) |
| `service.py` | Use cases |
| `domain.py` | Pure logic, no I/O |
| `ports.py` | Adapter interfaces |
| `models.py` | Persistence models |

Tables (`models.py`): `cards`, `drafts` (`body_hash` is what an approval must match), `agent_tasks` (ids like `HT-482-1`).
