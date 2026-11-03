# Loki

Loki reads the tenant-scoped PR context and diff, runs one read-only Codex review, and submits
structured findings for changed-file, skip-path, head, and count validation.

| File | Role |
|---|---|
| `agent.py` | Review flow, trust-boundary prompt, and structured response parsing |
| `runner.py` | Bounded Codex CLI invocation with no shell and read-only sandbox |
| `../_runtime.py` | Shared process runner: stdin prompts, time limits and output handling |
| `prompt.md` | Review rules and JSON output contract |
| `allowed_tools.py` | Exact `get_pr_context`, `get_diff`, and `save_findings` allowlist |
