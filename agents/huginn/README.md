# Huginn

Huginn consumes the transactional `huginn.run_repro` job created when intake emits
`ROOM_READY_FOR_REPRO`. It starts up to three paired attempts using the validated repository test
command, reads saved outcomes, and queues one aggregate verdict message through the Switch outbox.

## Skills

| Skill | When | Allowed tools | Outcome |
|---|---|---|---|
| `pick_test_command` | Before the first reproduction run for a pull request head. | `start_repro`, `get_repro` | Use the repository's validated command, not PR text; start one paired attempt. |
| `judge_repro` | A paired reproduction attempt finished and both saved outcomes are available. | `get_repro` | Decide reproduced, not covered, or setup failed from saved counts and failures only. |
| `fix_setup_failure` | Both modes failed for a setup reason rather than because the tests exercised the change. | `start_repro`, `get_repro` | Adjust only the command/env supported by repo configuration; respect the three-attempt cap. |

All three skills are required (`REQUIRED_SKILLS` in `config.py`).

| File | Role |
|---|---|
| `agent.py` | Bounded polling loop and deterministic verdict message |
| `runner.py` | Consumer of the `huginn` queue only, and tenant-scoped repro tool adapter, and holds the agent's Switch connection beside the worker (`../_presence_runner.py`) so it answers a maintainer's `ping`/`hello`, and tells anyone else to ask an admin to add them to `maintainers` in `heimdall.yml` |
| `prompt.md` | Role, untrusted-data rules, and output format |
| `allowed_tools.py` | Huginn's `start_repro`, `get_repro` and `get_file` MCP allowlist |
| `config.py` | `AGENT_NAME`, `MAX_TURNS = 12`, `MAX_REPRO_RUNS`, `BUDGET` (24k context, 150k run tokens, 40 KB per `get_diff`, 4 file reads, 3 repro runs), `REQUIRED_SKILLS`, `load_ai_config()` (`LLM_HUGINN_*`) |
| `skills/` | Three required playbooks (see table above) |

The verdict flow is deterministic. The worker builds Huginn's `AiAdapter` at startup, so a bad
model setting fails before any job runs. Polling is bounded by `MAX_POLLS` in `agent.py`.
