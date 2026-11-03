# audit

Urd's Well: the append-only audit log.

| File | Role |
|---|---|
| `api.py` | Public interface (the only file other modules import) |
| `service.py` | Use cases |
| `domain.py` | Pure logic, no I/O |
| `ports.py` | Adapter interfaces |
| `models.py` | Persistence models |

Tables (`models.py`): `audit_log` (insert-only: a trigger rejects UPDATE, DELETE and TRUNCATE), `usage` (agent token counts).

Public functions (`api.py`):

- `append_in_transaction(session, ctx, AuditEntry)` adds one `audit_log` row to the caller's
  transaction, so the entry commits or rolls back with the change it describes. It never commits.
- `list_for_pr(sessions, ctx, pr_ref)` returns a PR's entries oldest first, as `AuditView`s,
  for this installation only.
- `AuditEntry(actor, action, subject, payload)` is the frozen value `append_in_transaction` takes.
  `subject` for PR events is `pr_subject(pr_ref)` (`pr:<repo_id>#<number>`), so a PR's trail is
  one lookup on the `(installation_id, subject, at)` index. Object ids (draft, run, approval) go
  in `payload`, which holds flat ids, hashes and flags only, never PR text, diffs or secrets.
  It rejects blank or over-long fields (`MAX_FIELD_LENGTH`) and payloads over `MAX_PAYLOAD_BYTES`.

Actions written today (all by `approval`):

| Action | Actor | Payload |
|---|---|---|
| `draft.approved` | maintainer login or `policy:auto_suggest` | `draft_id`, `draft_hash`, `via` |
| `auto_suggest.paused` | maintainer login | `draft_id` |
| `repro.dispatch_authorized` | `policy:repro_dispatch` | `repro_run_id`, `approval_id`, `request_hash`, `head_sha` |

- `record_usage(sessions, ctx, UsageRecord)` saves one `usage` row with the tokens an agent run
  spent and the model string the provider reported. Agents call it once per run.
- `UsageRecord` is the frozen value it takes. It rejects a blank agent or model and negative counts.

Both tables carry `installation_id` for tenant-scoped reporting.
