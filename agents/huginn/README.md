# Huginn

Huginn consumes the transactional `huginn.run_repro` job created when intake emits
`ROOM_READY_FOR_REPRO`. It starts up to three paired attempts using the validated repository test
command, reads saved outcomes, and queues one aggregate verdict message through the Switch outbox.

| File | Role |
|---|---|
| `agent.py` | Bounded polling loop and deterministic verdict message |
| `runner.py` | Consumer of the `huginn` queue only, and tenant-scoped repro tool adapter, and holds the agent's Switch connection beside the worker (`../_presence_runner.py`) so it queues an `agent.mention` for each message a maintainer addresses to it, and tells anyone else to ask an admin to add them to `maintainers` in `heimdall.yml` |
| `prompt.md` | Role, untrusted-data rules, and output format |
| `allowed_tools.py` | Huginn's `start_repro` and `get_repro` MCP allowlist |
| `config.py` | `AGENT_NAME`, `MAX_TURNS = 12`, `MAX_REPRO_RUNS`, `BUDGET` (24k context, 150k run tokens, 40 KB per `get_diff`, 4 file reads, 3 repro runs), `load_ai_config()` (`LLM_HUGINN_*`) |

The verdict flow is deterministic. The worker builds Huginn's `AiAdapter` at startup, so a bad
model setting fails before any job runs. Polling is bounded by `MAX_POLLS` in `agent.py`.
