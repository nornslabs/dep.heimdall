# Heimdall

Heimdall assembles a current-head review card and approval-bound draft from stored findings and
reproduction results. Valid slash commands are parsed as data and dispatched to trusted handlers.

| File | Role |
|---|---|
| `agent.py` | Card assembly and command routing |
| `runner.py` | Consumer of the `heimdall` queue only: runs `heimdall.build_card`, then queues the approval message for the repo's room, and holds the agent's Switch connection beside the worker (`../_presence_runner.py`) so it answers a maintainer's `ping`/`hello`, and tells anyone else to ask an admin to add them to `maintainers` in `heimdall.yml` |
| `prompt.md` | Trust boundaries, allowed tools, and output format |
| `allowed_tools.py` | Heimdall's scoped MCP tool allowlist |
| `config.py` | `AGENT_NAME`, `MAX_TURNS = 8`, `load_ai_config()` (`LLM_HEIMDALL_*`) |

The card and command flows are deterministic today. The worker builds Heimdall's `AiAdapter` from
its own config at startup, so a bad model setting fails before any job runs.

`build_review_card` returns `approval_message`, which adds hidden draft identity and hash metadata
for the bridge. The marker is outside the saved draft body, so the approval hash still covers
only the exact text Gjallarhorn may post. Heimdall's MCP scope includes `create_mechanical_task`,
which saves a policy-gated task draft and returns its `heimdall-task` block.
