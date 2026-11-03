# approval

Approval records a maintainer's authorization for exact saved draft text and queues its GitHub
comment in the same transaction. Gjallarhorn rechecks the approval row and all draft hashes before
posting. Reproduction dispatches use a separate tenant-scoped policy approval row whose hash covers
the repository, workflow, ref, and all inputs; the GitHub sender checks it before dispatching.

Public functions: `record_approval`, `approve_by_policy`, `pause_auto_suggestions`,
`queue_repro_workflow_dispatch`, `verify_repro_workflow_dispatch`, `get_approved_draft`,
`validate_approval`, and `validate_posting_hash`.

| File | Role |
|---|---|
| `api.py` | Public interface; the only file other modules import |
| `service.py` | Maintainer and policy checks plus transactional approval persistence |
| `domain.py` | Pure draft approval checks and value types |
| `ports.py` | Adapter interfaces |
| `models.py` | Approval persistence models |

Tables `approvals` and `workflow_dispatch_approvals` hold tenant-scoped authorizations.
Workflow authorizations are unique per reproduction run and store the exact request hash.
