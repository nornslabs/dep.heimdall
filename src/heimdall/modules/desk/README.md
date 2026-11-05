# desk

Desk renders Switch summary cards for current PR heads, stores exact text drafts with SHA-256
approval hashes, and parses supported maintainer slash commands. It never posts to Switch or GitHub;
external effects remain behind the approval and outbox flows.

Public functions: `save_card`, `save_draft`, `create_mechanical_task`, `get_draft`, `get_latest_draft`,
`get_draft_in_transaction`, `get_current_draft_in_transaction`,
`render_card`, `render_mechanical_task_block`, `format_approval_message`, `mention_maintainers`, `hash_draft`,
and `parse_command` (`/heimdall approve` alone approves the newest draft in the thread).

| File | Role |
|---|---|
| `api.py` | Public interface; the only file other modules import |
| `service.py` | Transactional use cases |
| `domain.py` | Pure command parsing, card rendering, draft hashing and task block formatting |
| `ports.py` | Adapter interfaces |
| `models.py` | Persistence models |

Tables: `cards`, `drafts` (`body_hash` is what an approval must match), and `agent_tasks` (ids like
`HT-482-1`). Every use case scopes reads to its installation and verifies the PR head.
