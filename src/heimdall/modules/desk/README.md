# desk

Desk renders Switch summary cards for current PR heads, stores exact text drafts with SHA-256
approval hashes, and parses supported maintainer slash commands. It never posts to Switch or GitHub;
external effects remain behind the approval and outbox flows.

Public functions: `save_card`, `save_draft`, `render_card`, `hash_draft`, `parse_command`.

| File | Role |
|---|---|
| `api.py` | Public interface; the only file other modules import |
| `service.py` | Transactional use cases |
| `domain.py` | Pure command parsing, card rendering, draft hashing and approval metadata formatting |
| `ports.py` | Adapter interfaces |
| `models.py` | Persistence models |

Tables: `cards`, `drafts` (`body_hash` is what an approval must match), and `agent_tasks` (ids like
`HT-482-1`). Every use case scopes reads to its installation and verifies the PR head.
