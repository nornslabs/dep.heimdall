# review

Loki's review findings: save and validate.

Public functions: `validate_findings`, `save_findings`, `finish_review`, `list_findings`, and
`get_finding_in_transaction`.

| File | Role |
|---|---|
| `api.py` | Public interface (the only file other modules import) |
| `service.py` | Use cases |
| `_save.py` | Validate current head and persist accepted results |
| `_finish.py` | Finish the review step for a head when a review saved nothing |
| `domain.py` | Pure logic, no I/O |
| `ports.py` | Adapter interfaces |
| `models.py` | Persistence models |

Tables (`models.py`): `findings` (each cites `file:line`; indexed on `(pr_id, head_sha)`).

`save_findings(...)` rejects missing file/line data, paths outside the diff, skipped paths, and
findings beyond the configured limit. It stores accepted findings and finishes the review step
in one tenant transaction. Repeated saves on the same head do not duplicate findings.

`finish_review(...)` finishes the review step for a head without saving findings, so a clean
pass or a docs-only skip still lets triage build the card. It refuses a head the PR has moved
past.

`get_finding_in_transaction(...)` returns a finding scoped to the current tenant, PR and head
within an existing transaction so another module can validate a mechanical task without opening
a nested transaction.
