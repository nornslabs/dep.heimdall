# PRD: HEI-92 — Loki on the agentic runtime

|| Field | Value |
| --- | --- | --- |
|| Issue | HEI-92 |
|| Parent | HEI-83 — Make Huginn, Loki and Heimdall real tool-using agents |
|| Depends on | HEI-84 (runtime), HEI-85 (shared guardrails), HEI-88 (skills), HEI-89 (output schemas) |
|| Blocks | HEI-94 (conversations) |
|| Suggested branch | `priyankarpal/hei-92-7-loki-on-the-agentic-runtime` |
|| Design reference | `Heimdall agents agentic design.md`, step 7 |
|| Status | Implemented |

## 1. Summary

Move Loki from its fixed review flow onto the shared runtime (`agents/_runtime.py`). After this
change the model chooses its steps; the ToolBox, budgets, guardrails and `submit_review` decide
what it may do and what a finished review looks like.

The existing outbox path, review validation, `LokiReview` schema and `review_problems` cross-check
are **unchanged**. Only the execution inside the job task is replaced.

## 2. What changes

|| Part | File | Before | After |
| --- | --- | --- | --- | --- |
|| Context plan | `agents/loki/context.py` | `build_review_brief` / `build_messages` for the fixed flow | Same brief, now wrapped as `pr_data` and fed to the runtime as the system + user turn |
|| Guardrails | `agents/loki/guardrails.py` | `FindingGuard` used inline by the fixed flow | `FindingGuard` (added-line citations, docs-only skip) plus `submit_review_tool` with the saved-id cross-check |
|| Agent entry point | `agents/loki/agent.py` | Fixed review loop | `run_loki(...)` delegating to `run_agent` |
|| Allowed tools | `agents/loki/allowed_tools.py` | `get_pr_context`, `get_diff`, `save_findings`, `recall` | Same set plus `get_file` (HEI-86) |
|| Config | `agents/loki/config.py` | Existing constants | `MAX_TURNS = 8`, `MAX_FINDINGS = 5`, budget (40k context, 250k run tokens, 80 KB per `get_diff`, 8 file reads) |
|| Runner wiring | `agents/loki/runner.py` | Fixed flow that posts the review | Builds brief, toolbox and guardrails; calls `run_loki`; queues one room post through the outbox |
|| README | `agents/loki/README.md` | Describes fixed flow | Describes agentic runtime |

## 3. Wakes on

|| Event | Goal |
| --- | --- |
| `pr.ready_for_review` | Produce one `LokiReview`: saved finding ids, or a skip reason |

## 4. Context plan (`context.py`)

```
1. PR brief (installation_id, pr_id, repo, number, title, body, head_sha, base_sha)
2. Changed test files first, then the remaining source files (each list capped at MAX_BRIEF_FILES = 40)
3. Linked issue, when intake found one (title and body are untrusted text)
4. docs_only flag: every changed path matches review.skip_paths
5. Maintainer notes from recall, for this repo
```

Everything goes into the user turn as `pr_data` via `wrap_pr_data`. PR title, body, issue text
and file paths are data inside the wrapper; the model never treats them as instructions. File
content stays behind `get_diff` and `get_file`, where the runtime applies the byte and context caps.

## 5. Tools

|| Tool | Source |
| --- | --- |
| `get_pr_context` | MCP — PR metadata (number, title, body, head/base SHA) |
| `get_diff` | MCP — unified diff, 80 KB result cap per read |
| `get_file` | MCP — line range at the PR's head or base (HEI-86) |
| `save_findings` | MCP — save a confirmed finding, returns its id |
| `recall` | MCP — read-only maintainer notes kept for this repo |
| `load_skill` | Local meta-tool (runtime-provided) |
| `submit_review` | Local submit tool (bound to `LokiReview`) |

No reproduction controls and no memory write tool. Loki reviews the diff only; Huginn reproduces.

## 6. Guardrails (`guardrails.py`)

### 6.1 Citations must be added lines in this diff

`FindingGuard.refusal` refuses `save_findings` when any cited `file:line` is not an added line in
this PR's diff (computed by `diff_added_lines` in `agents/loki/_diff.py`). A refused call reaches
the model as `error: ...`, so it can correct itself instead of crashing the job. At most
`MAX_REFUSED_CITATIONS = 5` refused citations are named in one error line.

### 6.2 Docs-only skip

When every changed path matches `review.skip_paths`, the PR is docs-only: `save_findings` is
refused ("save nothing and submit skipped_reason 'docs_only'") and the submit cross-check requires
`skipped_reason == "docs_only"`. A PR that changes non-skipped paths may not submit `docs_only`.

### 6.3 Saved-id check

`review_problems(review, saved_ids)` refuses any finding id `save_findings` did not accept during
this run. It shares the runtime's one repair round with schema failures, so a review citing an
unsaved id is corrected or the run ends without a posted answer.

### 6.4 One outcome

`LokiReview` (pydantic, `extra="forbid"`, frozen) requires findings or a skip reason, never both
and never neither, via its model validator.

## 7. Output

`LokiReview` (defined in `agents/loki/schemas.py`, unchanged):

|| Field | Meaning |
| --- | --- |
| `finding_ids` | ids `save_findings` returned, at most `MAX_FINDINGS = 5` |
| `skipped_reason` | `docs_only`, `no_findings` or null |
| `summary` | 1–400 characters, tone-filtered |

A review with findings reports those ids; a clean pass submits `skipped_reason = "no_findings"`;
a docs-only PR submits `skipped_reason = "docs_only"` with no findings.

The renderer `render_review` in `agents/loki/_message.py` builds the room message from validated
fields only — counts plus the bounded summary, never PR text. The runner queues one `room.message`
outbox row per PR head, idempotency key
`loki:<installation_id>:<pr_id>:<head_sha>:review`.

## 8. Limits

|| Setting | Value |
| --- | --- |
| `MAX_TURNS` | 8 per review |
| `CONTEXT_TOKENS` | 40 000 |
| `RUN_TOKEN_BUDGET` | 250 000 |
| `MAX_DIFF_BYTES` | 80 000 per `get_diff` |
| `MAX_FILE_READS` | 8 (`get_file` call cap) |
| `MAX_FINDINGS` | 5 |

`call_caps["get_file"]` is 8; the diff byte cap is in `max_result_bytes`.

## 9. Model

`LLM_LOKI_*` with `LLM_DEFAULT_*` fallback, via `load_ai_config()` (unchanged). At startup Loki
logs `loki_same_model_as_other_agents` when its model string matches Huginn's or Heimdall's — a
warning, not a failure.

## 10. What does not change

- The review module's validation of changed files, skipped paths, the finding cap and the head.
- `LokiReview`, `SUBMIT_REVIEW`, `review_problems` in `schemas.py`.
- `render_review` in `_message.py`.
- The four skill files in `agents/loki/skills/`.
- `REQUIRED_SKILLS`, `load_ai_config`, `load_switch_config` in `config.py`.
- The outbox path, approval rules and GitHub write rules.

## 11. Acceptance criteria

|| # | Criterion |
| --- | --- | --- |
|| AC-1 | On seeded PRs, a scripted model drives Loki to submit `finding_ids` drawn only from ids `save_findings` returned this run. |
|| AC-2 | A citation outside this PR's added lines is refused before the finding is saved. |
|| AC-3 | A docs-only PR is submitted as `skipped_reason = "docs_only"` with no findings. |
|| AC-4 | `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright` and `uv run pytest tests/unit` pass. |
|| AC-5 | No new dependency. |

## 12. Test plan

### Unit — `tests/unit/agents/test_loki_runtime.py`

No DB, no network. Use `ScriptedAi` and fake tool handlers.

|| Test | Verifies |
| --- | --- |
| `test_review_saves_findings_and_submits` | A scripted model calls `save_findings` then `submit_review(finding_ids=…)`; run completes as `Completed`. |
| `test_docs_only_pr_submits_skip` | The brief says docs_only; the model submits `skipped_reason = "docs_only"` with no finding ids. |
| `test_unsaved_finding_id_gets_repair_round` | `submit_review` with an unknown id triggers one repair round; the corrected call completes. |
| `test_citation_outside_diff_is_refused` | `save_findings` citing a line not added in the diff returns `error:`. |
| `test_no_findings_and_no_reason_is_rejected` | The schema rejects a review with neither findings nor a skip reason. |

### Unit — `tests/unit/agents/test_loki_guardrails.py`

|| Test | Verifies |
| --- | --- |
| `test_citation_not_added_line_is_refused` | `FindingGuard.refusal` names the offending `file:line`. |
| `test_docs_only_saves_nothing` | The guard refuses `save_findings` for a docs-only PR. |
| `test_unsaved_id_rejected_on_submit` | `submission_problems` names an id this run did not save. |

### Unit — `tests/unit/agents/test_loki_schemas.py`

|| Test | Verifies |
| --- | --- |
| `test_rejects_both_findings_and_skip` | `LokiReview` with both `finding_ids` and `skipped_reason` is invalid. |
| `test_rejects_neither` | `LokiReview` with neither is invalid. |
| `test_findings_capped_at_five` | More than five ids is invalid. |

### Repository invariant — `tests/unit/agents/test_shipped_skills.py`

Already parametrised over `AgentName`; asserts Loki's required skills:
`edge_cases`, `scope_check`, `test_quality`, `mechanical_tagging`, and that every body is
non-empty and under the 8,000-character cap.

## 13. Security notes

- PR content (title, body, diff, issue text, file content) is wrapped in `pr_data`; the model
  receives it as data, never as instructions.
- Loki has no write tool to GitHub and no memory write tool; `recall` is read only, so PR text
  cannot plant a note that outlives the PR.
- `save_findings` cites only added lines in this PR's diff, enforced before the call runs.
- Findings and the summary are tone-filtered and bounded before the room post is built.
- The model's assistant reply is never posted; only validated fields are rendered.

## 14. Rollout

- No database migration and no configuration variable.
- Rebuild and recreate the `loki` service. Its runner loads and validates skills at startup
  before opening the queue, so a broken playbook fails fast.
- If a run stops producing answers, check `loki_did_not_submit` in the worker logs.

## 15. Resolved questions

1. **Does Loki reproduce the fix?** No. That is Huginn's job; Loki reviews the current diff only.
2. **Can Loki edit files?** No. Its allowed tools are read-only:
   `get_pr_context`, `get_diff`, `get_file`, `save_findings`, `recall`.
3. **Where do findings live?** `save_findings` (MCP) stores them for the PR head; the submit
   cross-check knows only this run's saved ids.
4. **What happens to a docs-only PR?** It gets `skipped_reason = "docs_only"` and no findings;
   the review step still finishes so triage can move to `card_posted`.
