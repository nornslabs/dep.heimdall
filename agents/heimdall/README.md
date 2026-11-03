# Heimdall

Heimdall assembles a current-head review card and approval-bound draft from stored findings and
reproduction results. Valid slash commands are parsed as data and dispatched to trusted handlers.

| File | Role |
|---|---|
| `agent.py` | Card assembly and command routing |
| `prompt.md` | Trust boundaries, allowed tools, and output format |
| `allowed_tools.py` | Heimdall's scoped MCP tool allowlist |

`build_review_card` returns `approval_message`, which adds hidden draft identity and hash metadata
for the bridge. The marker is outside the saved draft body, so the approval hash still covers
only the exact text Gjallarhorn may post. Heimdall's MCP scope includes `create_mechanical_task`,
which saves a policy-gated task draft and returns its `heimdall-task` block.
