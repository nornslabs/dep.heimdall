# tenancy

Installations, repos and layered heimdall.yml config.

| File | Role |
|---|---|
| `api.py` | Public interface (the only file other modules import) |
| `service.py` | Use cases |
| `domain.py` | Pure logic, no I/O |
| `ports.py` | Adapter interfaces |
| `models.py` | Persistence models |

Tables (`models.py`): `installations` (the tenant; `installation_id` is the key), `repos` (effective heimdall.yml in `config`).
