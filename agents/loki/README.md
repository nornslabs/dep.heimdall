# Loki

Loki consumes the transactional `loki.run_review` job queued for a PR head. It runs on the shared
runtime (`agents/_runtime.py`): the model chooses what to read, the toolbox decides what it may
call, and only `submit_review` ends the run.

## How a run goes

1. `context.py` builds the brief: the PR, then its changed test files, then the source they
   cover, then the linked issue. The brief also says whether the PR is docs-only.
2. The model reads what it needs with `get_pr_context`, `get_diff` and `get_file`, loading a
   skill when its next step matches one, and saves each confirmed finding with `save_findings`.
3. `guardrails.py` refuses a finding whose `file:line` is not an added line in the diff, and
   saves nothing at all for a docs-only PR.
4. It finishes with `submit_review`, citing the ids `save_findings` returned or the reason there
   are none. The cross-check refuses an id this run did not save and a review that contradicts
   the docs-only decision, using the runtime's one repair round.
5. The runner renders the room message from the validated fields and queues it through the
   Switch outbox, once per PR head.

The review module still validates changed files, skipped paths, the finding cap and the head
before it stores anything. Loki's guard checks the one thing only the diff can answer: which
line the PR added.

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
| `agent.py` | `run_loki`: one runtime run with Loki's config and review schema |
| `context.py` | The brief: PR metadata, changed test files, then source, then the linked issue |
| `guardrails.py` | `FindingGuard`: a finding must cite an added line in the diff, a docs-only PR is skipped; `guard_save_findings`, `submit_review_tool` |
| `_diff.py` | Private: which new-side lines each changed path adds, so a citation can be checked |
| `runner.py` | Consumer of the `loki` queue only: builds the run, then queues one room post as Loki through the outbox, and holds the agent's Switch connection beside the worker (`../_presence_runner.py`) so it answers a maintainer's `ping`/`hello` and `remember`/`forget` commands (`../_memory_handler.py`), and tells anyone else to ask an admin to add them to `maintainers` in `heimdall.yml` |
| `_message.py` | Private: the room post from a validated review — counts plus its bounded summary, never PR text |
| `_notes.py` | Private: maintainer notes for the repo, read through `recall` and wrapped as PR data |
| `schemas.py` | `LokiReview` submit schema and `SUBMIT_REVIEW`, plus `review_problems` (every id must have been saved for this head) |
| `config.py` | `AGENT_NAME`, `MAX_TURNS = 8`, `MAX_FINDINGS = 5`, `BUDGET` (40k context, 250k run tokens, 80 KB per `get_diff`, 8 file reads), `REQUIRED_SKILLS`, `load_ai_config()` (`LLM_LOKI_*`) |
| `../_mcp_tools.py` | In-process clients for the scoped MCP tool servers (shared with Huginn and Heimdall) |
| `../_runtime.py` | Shared agent runtime (`run_agent`, `ToolBox`, `RunUsage`, `still_current`); see `../README.md` |
| `../_lifecycle.py` | The `loki_same_model_as_other_agents` warning |
| `prompt.md` | Role, untrusted-data rules, citation rule, limits, and the skill index the runtime adds |
| `allowed_tools.py` | Exact `get_pr_context`, `get_diff`, `get_file`, `save_findings` and read-only `recall` allowlist |
| `skills/` | Four required playbooks (see table above) |

A run that does not reach a review logs `loki_did_not_submit` and posts nothing. At startup Loki
logs `loki_same_model_as_other_agents` when its model string matches Huginn's or Heimdall's.
This is a warning, not a failure.
