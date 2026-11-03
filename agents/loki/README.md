# Loki

Loki reads the tenant-scoped PR context and diff, runs one provider-neutral review, and submits
structured findings for changed-file, skip-path, head, and count validation.

| File | Role |
|---|---|
| `agent.py` | Review flow, trust-boundary prompt, and structured response parsing |
| `runner.py` | Review runner backed by the shared HTTP AI adapter |
| `config.py` | Loki-specific provider settings with `LLM_DEFAULT_*` fallback |
| `../_runtime.py` | Bounded tool loop, MCP allowlist/tenant scope, retries and usage callback |
| `prompt.md` | Review rules and JSON output contract |
| `allowed_tools.py` | Exact `get_pr_context`, `get_diff`, and `save_findings` allowlist |

The provider is configured with `LLM_LOKI_*`; each unset value falls back to
`LLM_DEFAULT_*`. The MCP toolbox hides `installation_id` from the model and
injects the active tenant on every tool call.
