# approval

Approval records the maintainer's authorization for exact saved draft text. It loads maintainer
policy from the repository's effective config, verifies the draft hash and active state, and writes
one tenant-scoped approval and idempotent GitHub outbox message in one transaction. Gjallarhorn
rechecks the approval row, stored draft hash, outbox hash, and recomputed body hash before posting.

Public functions: `record_approval`, `approve_by_policy`, `pause_auto_suggestions`,
`get_approved_draft`, `validate_approval`, and `validate_posting_hash`.

| File | Role |
|---|---|
| `api.py` | Public interface; the only file other modules import |
| `service.py` | Maintainer and hash checks plus transactional approval persistence |
| `domain.py` | Pure approval checks and value types |
| `ports.py` | Adapter interfaces |
| `models.py` | Approval persistence model |

Table `approvals` has one row per draft and records `approver`, `draft_hash`, and `via`.
