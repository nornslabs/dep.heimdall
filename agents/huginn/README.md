# Huginn

Huginn starts and monitors one bounded reproduction attempt, then formats the stored verdict for a
Switch room draft. The configured repository test command is supplied by trusted application code.

| File | Role |
|---|---|
| `agent.py` | Bounded polling loop and deterministic verdict message |
| `prompt.md` | Role, untrusted-data rules, and output format |
| `allowed_tools.py` | Huginn's `start_repro` and `get_repro` MCP allowlist |
