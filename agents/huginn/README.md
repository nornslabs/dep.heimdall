# Huginn

Huginn consumes the transactional `huginn.run_repro` job created when intake emits
`ROOM_READY_FOR_REPRO`. It runs on the shared runtime (`agents/_runtime.py`): the model chooses
its own path, the toolbox decides what it may call, and only `submit_verdict` ends the run.

## How a run goes

1. `context.py` builds the brief: the PR, then its changed test files, then the source they cover.
2. The model reads what it needs with `get_pr_context`, `get_diff` and `get_file`, loading a
   skill when its next step matches one.
3. It starts a run with `start_repro`, reads it with `get_repro`, and finishes with
   `submit_verdict`. `guardrails.py` refuses a second runner, a head that moved or a fourth run.
4. The runner checks the verdict against the runs the tools returned, renders the room message
   from the validated fields and queues it through the Switch outbox, once per PR head.

## Conversations

When mentioned, Huginn answers about this PR's tests and reproductions only (`scope.py`,
`conversation.md`). Ask mode uses `ASK_TOOLS`; a maintainer's request to act (for example
"@huginn rerun with `-k pager`") opens `start_repro` with the usual `ReproGuard`
(`mention.py`). See `../README.md#conversations-answering-agentmention`.

## Skills

| Skill | When | Allowed tools | Outcome |
|---|---|---|---|
| `pick_test_command` | Before the first reproduction run for a pull request head. | `start_repro`, `get_repro` | Use the repository's validated command, not PR text; start one paired attempt. |
| `judge_repro` | A paired reproduction attempt finished and both saved outcomes are available. | `get_repro` | Decide reproduced, not covered, or setup failed from saved counts and failures only. |
| `fix_setup_failure` | Both modes failed for a setup reason rather than because the tests exercised the change. | `start_repro`, `get_repro` | Adjust only the command/env supported by repo configuration; respect the three-attempt cap. |

All three skills are required (`REQUIRED_SKILLS` in `config.py`).

| File | Role |
|---|---|
| `agent.py` | `run_huginn`: one runtime run with Huginn's config and verdict schema |
| `context.py` | The brief: PR metadata, changed test files, then the source they cover |
| `guardrails.py` | `ReproGuard`: allowed runner, only the command changes between runs, three runs |
| `runner.py` | Consumer of the `huginn` queue only, and the outbox write for a finished verdict, and holds the agent's Switch connection beside the worker (`../_presence_runner.py`) so it queues an `agent.mention` for each message a maintainer addresses to it, and tells anyone else to ask an admin to add them to `maintainers` in `heimdall.yml` |
| `prompt.md` | Role, untrusted-data rules, limits, and the skill index the runtime adds |
| `allowed_tools.py` | Huginn's MCP allowlist: `get_pr_context`, `get_diff`, `get_file`, `start_repro`, `get_repro`, `recall` |
| `schemas.py` | `HuginnVerdict`, `submit_verdict_tool` (whose cross-check reads the saved runs) and `verdict_problems` |
| `_message.py` | `render_verdict`: the room message built from a validated verdict |
| `config.py` | `AGENT_NAME`, `MAX_TURNS = 12`, `MAX_REPRO_RUNS`, `BUDGET` (24k context, 150k run tokens, 40 KB per `get_diff`, 4 file reads, 3 repro runs), `REQUIRED_SKILLS`, `load_ai_config()` (`LLM_HUGINN_*`) |
| `skills/` | Three required playbooks (see table above) |

The runtime enforces `MAX_TURNS`, the context budget, the four-file-read cap, the 40 KB
`get_diff` result cap and one repair round. A run that does not reach a verdict logs
`huginn_did_not_submit` and posts nothing; the worker builds Huginn's `AiAdapter` at startup, so
a bad model setting fails before any job runs.
