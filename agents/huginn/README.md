# Huginn

Huginn consumes the transactional `huginn.run_repro` job created when intake emits
`ROOM_READY_FOR_REPRO`. It starts up to three paired attempts using the validated repository test
command, reads saved outcomes, and queues one aggregate verdict message through the Switch outbox.

| File | Role |
|---|---|
| `agent.py` | Bounded polling loop and deterministic verdict message |
| `runner.py` | Queue consumer and tenant-scoped repro tool adapter |
| `prompt.md` | Role, untrusted-data rules, and output format |
| `allowed_tools.py` | Huginn's `start_repro` and `get_repro` MCP allowlist |
| `config.py` | Huginn-specific provider settings with `LLM_DEFAULT_*` fallback |

Provider settings use `LLM_HUGINN_*` with field-by-field fallback to
`LLM_DEFAULT_*`. The shared MCP toolbox exposes only Huginn's allowlist and
injects the active installation id into every tool call.
