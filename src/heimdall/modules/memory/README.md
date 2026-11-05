# memory

Durable notes each agent keeps per repository, such as "tests run with `pytest -q tests/unit`"
or "`docs/` is out of scope for review". A note outlives the PR it was made on, so the agent can
recall it on the next PR in the same repo.

Public functions: `remember_note`, `recall_notes`, `forget_note`, the
`remember_note_in_transaction` / `forget_note_in_transaction` variants for a caller that
writes its reply in the same transaction, plus the pure rules
`validate_note`, `validate_key` and `matches_query`.

| File | Role |
|---|---|
| `api.py` | Public interface (the only file other modules import) |
| `service.py` | Use cases |
| `_write.py` | Remember and forget; the maintainer check |
| `_read.py` | Recall and row-to-value mapping |
| `domain.py` | Pure rules: key format, 300-character value limit, query matching |
| `models.py` | Persistence models |

Tables (`models.py`): `agent_memory` (one value per `(repo_id, agent, key)`; indexed on
`(installation_id, repo_id, agent)`).

**Guardrail.** `remember_note` and `forget_note` refuse with `NotAMaintainer` unless
`requested_by` is on the repo's maintainer list, and with `RepoNotFound` unless the repo is
enabled for the caller's installation. The check lives here, not in the agent or its tools, so a
model steered by PR text cannot write or drop a note on its own.

- `remember_note` replaces a note with the same key (keys are case-insensitive) and records who
  asked and from which message.
- `recall_notes` returns unexpired notes ordered by key; a query matches the key or value,
  ignoring case. `expires_at` is never set yet; it exists for a later TTL.
- `forget_note` raises `NoteNotFound` when no note has that key.

Note values are written by maintainers but are still shown to models as data, never as
instructions.
