# Loki

Loki reads the tenant-scoped PR context and diff and makes one model call through its own
`AiAdapter`. The call has no tools, so the model can only answer. Loki then submits the structured
findings for changed-file, skip-path, head and count validation. Before saving, Loki reads the PR
head again and drops its findings if the head has moved.

| File | Role |
|---|---|
| `agent.py` | Review flow, trust-boundary messages, `main()` |
| `_findings.py` | Private parsing of the model reply into findings (capped at `MAX_FINDINGS`) |
| `config.py` | `AGENT_NAME`, `MAX_TURNS = 8`, `MAX_FINDINGS = 5`, `load_ai_config()` (`LLM_LOKI_*`) |
| `../_runtime.py` | Shared `ToolBox`, `run_tool_loop`, `RunUsage`, `still_current` |
| `../_lifecycle.py` | `wait_until_stopped` and the `loki_same_model_as_other_agents` warning |
| `prompt.md` | Review rules and JSON output contract |
| `allowed_tools.py` | Exact `get_pr_context`, `get_diff`, and `save_findings` allowlist |

At startup Loki logs `loki_same_model_as_other_agents` when its model string matches Huginn's or
Heimdall's. This is a warning, not a failure.
