# Loki

Loki reads the tenant-scoped PR context and diff and makes one model call through its own
`AiAdapter`. The call has no tools, so the model can only answer. Loki then submits the structured
findings for changed-file, skip-path, head and count validation. Before saving, Loki reads the PR
head again and drops its findings if the head has moved.

| File | Role |
|---|---|
| `agent.py` | Review flow and trust-boundary messages |
| `runner.py` | Consumer of the `loki` queue only: runs `loki.run_review`, dropping jobs for an old head, then queues one room post as Loki through the outbox, and holds the agent's Switch connection beside the worker (`../_presence_runner.py`) so it answers a maintainer's `ping`/`hello` |
| `_message.py` | Private: the room post, counts only (no PR or model text), so nothing untrusted is posted under Loki's name |
| `_findings.py` | Private parsing of the model reply into findings (capped at `MAX_FINDINGS`) |
| `config.py` | `AGENT_NAME`, `MAX_TURNS = 8`, `MAX_FINDINGS = 5`, `load_ai_config()` (`LLM_LOKI_*`) |
| `../_mcp_tools.py` | In-process clients for the scoped MCP tool servers (shared with Heimdall) |
| `../_runtime.py` | Shared `ToolBox`, `run_tool_loop`, `RunUsage`, `still_current` |
| `../_lifecycle.py` | The `loki_same_model_as_other_agents` warning |
| `prompt.md` | Review rules and JSON output contract |
| `allowed_tools.py` | Exact `get_pr_context`, `get_diff`, and `save_findings` allowlist |

At startup Loki logs `loki_same_model_as_other_agents` when its model string matches Huginn's or
Heimdall's. This is a warning, not a failure.
