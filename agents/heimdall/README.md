# Heimdall

Heimdall assembles a current-head review card and approval-bound draft from stored findings and
reproduction results. Valid slash commands are parsed as data and dispatched to trusted handlers.

| File | Role |
|---|---|
| `agent.py` | Card assembly and command routing |
| `prompt.md` | Trust boundaries, allowed tools, and output format |
| `allowed_tools.py` | Heimdall's scoped MCP tool allowlist |
