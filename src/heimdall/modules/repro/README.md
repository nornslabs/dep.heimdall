# repro

Reproduction runs on GitHub Actions and their verdicts.

| File | Role |
|---|---|
| `api.py` | Public interface (the only file other modules import) |
| `service.py` | Use cases |
| `domain.py` | Pure logic, no I/O |
| `ports.py` | Adapter interfaces |
| `models.py` | Persistence models |

Tables (`models.py`): `repro_runs` (unique `(pr_id, head_sha, attempt)`; `with_fix` / `without_fix` results and `verdict`).
