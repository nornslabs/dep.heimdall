# Loki

Loki reads the tenant-scoped PR context and diff and makes one model call through its own
`AiAdapter`. The call has no tools, so the model can only answer. Loki then submits the structured
findings for changed-file, skip-path, head and count validation. Before saving, Loki reads the PR
head again and drops its findings if the head has moved.

## Skills

| Skill | When | Allowed tools | Outcome |
|---|---|---|---|
| `edge_cases` | Reviewing a diff for unhandled inputs, empty values, limits or failure paths. | `get_pr_context`, `get_diff` | Produce cited `file:line` findings for missing input handling. |
| `scope_check` | Reviewing whether the pull request does the requested work and nothing unrelated. | `get_pr_context`, `get_diff` | Flag out-of-scope files or behaviour not required by the issue. |
| `test_quality` | Reviewing tests added or changed by the pull request. | `get_diff` | Flag tests that cannot fail before the fix or do not exercise the change. |
| `mechanical_tagging` | Saving a finding that may belong to an `auto_suggest` category. | `save_findings` | Assign exactly one allowed mechanical category, or none. |

All four skills are required (`REQUIRED_SKILLS` in `config.py`).

| File | Role |
|---|---|
| `agent.py` | Review flow and trust-boundary messages |
| `runner.py` | Consumer of the `loki` queue only: runs `loki.run_review`, dropping jobs for an old head, then queues one room post as Loki through the outbox, and holds the agent's Switch connection beside the worker (`../_presence_runner.py`) so it answers a maintainer's `ping`/`hello` and `remember`/`forget` commands (`../_memory_handler.py`), and tells anyone else to ask an admin to add them to `maintainers` in `heimdall.yml` |
| `_message.py` | Private: the room post from a validated review — counts plus its bounded summary, never PR text |
| `schemas.py` | `LokiReview` submit schema and `SUBMIT_REVIEW`, plus `review_problems` (every id must have been saved for this head) |
| `_findings.py` | Private parsing of the model reply into findings (capped at `MAX_FINDINGS`) |
| `config.py` | `AGENT_NAME`, `MAX_TURNS = 8`, `MAX_FINDINGS = 5`, `BUDGET` (40k context, 250k run tokens, 80 KB per `get_diff`, 8 file reads), `REQUIRED_SKILLS`, `load_ai_config()` (`LLM_LOKI_*`) |
| `../_mcp_tools.py` | In-process clients for the scoped MCP tool servers (shared with Heimdall) |
| `../_runtime.py` | Shared agent runtime (`run_agent`, `ToolBox`, `RunUsage`, `still_current`); see `../README.md` |
| `../_lifecycle.py` | The `loki_same_model_as_other_agents` warning |
| `prompt.md` | Review rules and JSON output contract |
| `allowed_tools.py` | Exact `get_pr_context`, `get_diff`, `get_file` and `save_findings` allowlist |
| `skills/` | Four required playbooks (see table above) |

At startup Loki logs `loki_same_model_as_other_agents` when its model string matches Huginn's or
Heimdall's. This is a warning, not a failure.
