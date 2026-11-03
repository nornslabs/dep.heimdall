# review

Loki's review findings: save and validate.

| File | Role |
|---|---|
| `api.py` | Public interface (the only file other modules import) |
| `service.py` | Use cases |
| `_save.py` | Validate current head and persist accepted results |
| `domain.py` | Pure logic, no I/O |
| `ports.py` | Adapter interfaces |
| `models.py` | Persistence models |

Tables (`models.py`): `findings` (each cites `file:line`; indexed on `(pr_id, head_sha)`).

`save_findings(...)` rejects missing file/line data, paths outside the diff, skipped paths, and
findings beyond the configured limit. It stores accepted findings and finishes the review step
in one tenant transaction. Repeated saves on the same head do not duplicate findings.
