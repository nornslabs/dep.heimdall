# PRD: HEI-93 — Heimdall on the agentic runtime

| Field | Value |
| --- | --- |
| Issue | HEI-93 |
| Parent | HEI-83 — Make Huginn, Loki and Heimdall real tool-using agents |
| Depends on | HEI-84 (runtime), HEI-85 (shared guardrails), HEI-88 (skills), HEI-89 (output schemas) |
| Blocks | HEI-96 (acceptance tests and LLD update) |
| Suggested branch | `priyankarpal/hei-93-8-heimdall-on-the-agentic-runtime` |
| Design reference | `Heimdall agents agentic design.md`, step 8 |

## 1. Summary

Move Heimdall from its current fixed flow (`build_review_card` / `handle_command` in
`agents/heimdall/agent.py`) onto the shared runtime (`agents/_runtime.py`). After this change
the model chooses its steps; the ToolBox, budgets, guardrails and `submit_result` decide what
it may do and what a finished answer looks like.

The existing `runner.py` wire-up, the approval hash logic, the outbox path and the
`DeskResult` schema are **unchanged**. Only the execution inside the job task is replaced.

## 2. What changes

| Part | File | Before | After |
| --- | --- | --- | --- |
| Context plan | `agents/heimdall/context.py` | _(new)_ | Brief, `get_repro`, `list_findings`, then command text |
| Own guardrails | `agents/heimdall/guardrails.py` | _(new)_ | Maintainer-only commands; ≤ 2 `save_draft` calls; findings quoted by saved id only |
| Agent entry point | `agents/heimdall/agent.py` | `build_review_card` + `handle_command` fixed flow | `run_heimdall(...)` delegating to `run_agent` |
| Allowed tools | `agents/heimdall/allowed_tools.py` | 6 tools | Add `get_pr_context`, `submit_desk_result` |
| Config | `agents/heimdall/config.py` | Existing constants | Add `MAX_DRAFTS = 2`; clarify no diff/file access |
| Runner wiring | `agents/heimdall/runner.py` | Calls `build_review_card` directly | Builds context, toolbox, guardrails; calls `run_heimdall` |
| README | `agents/heimdall/README.md` | Describes fixed flow | Updated to describe agentic runtime |

## 3. Wakes on

| Event | Goal |
| --- | --- |
| `pr.ready_for_card` | Produce one `DeskResult` with `action="card"` |
| `maintainer.command` | Produce one `DeskResult` per command (draft, task, or explanation) |

## 4. Context plan (`context.py`)

```
1. PR brief (installation_id, pr_id, repo, number, title, head_sha)
2. get_repro result for the latest run (when one exists)
3. list_findings result for the head
4. Command text (for maintainer.command wake-ups); omitted for pr.ready_for_card
```

The brief goes in a `pr_data` wrapper via `wrap_pr_data`. Tool results at step 2–3 are fetched
before the first model call (eager context) rather than left for the model to discover, keeping
the brief self-contained and the first model turn focused.

## 5. Tools

| Tool | Source |
| --- | --- |
| `get_pr_context` | MCP — PR metadata and current head |
| `get_repro` | MCP — latest reproduction result |
| `list_findings` | MCP — accepted findings for the head |
| `save_card` | MCP — save the Switch card |
| `save_draft` | MCP — save a draft and get its hash |
| `create_mechanical_task` | MCP — create a policy-gated task |
| `load_skill` | Local meta-tool (runtime-provided) |
| `submit_desk_result` | Local submit tool (bound to `DeskResult`) |

No `get_diff`, no `get_file`. Heimdall reads only stored results.

## 6. Guardrails (`guardrails.py`)

### 6.1 Maintainer-only commands

A `maintainer.command` wake-up carries the sender's GitHub login. The guardrail checks the
login against the repository's `maintainers` list (from `tenancy.api.repo_maintainers`) before
the job starts. A non-maintainer command returns an `error:` without starting the agent loop.

### 6.2 Draft cap

`save_draft` is capped at `MAX_DRAFTS = 2` per command run via the budget's `call_caps`. The
third call returns `error: save_draft: at most 2 drafts per command`.

This is enforced by `AgentBudget.call_caps` already (the existing budget sets it). The guardrail
is a named constant clarification, not new mechanism.

### 6.3 Finding id check

The `DeskResult` submit cross-check (`desk_result_problems`) already refuses a `draft_id` or
`card_id` not saved by this run. The guardrail that **drafts quote findings by existing id only**
is enforced by the cross-check on submission, not at `save_draft` time — the model cites finding
ids in the draft body, and the cross-check verifies the submitted `draft_id` was saved.

> **Note:** The finding-id text check (whether the draft body actually *contains* a cited id) is
> a prompt concern, not a code guardrail; code cannot parse free-form prose reliably.

## 7. Output

`DeskResult` (defined in `agents/heimdall/schemas.py`, unchanged):

| `action` | Meaning |
| --- | --- |
| `card` | `card_id` names the saved card |
| `draft` | `draft_id` names the saved draft |
| `mechanical_task` | `task_id` names the saved task |
| `explanation` | `explanation` text (≤ 800 chars, tone-filtered) |

## 8. Limits

| Setting | Value |
| --- | --- |
| `MAX_TURNS` | 8 per command |
| `CONTEXT_TOKENS` | 24 000 |
| `RUN_TOKEN_BUDGET` | 100 000 |
| `call_caps["save_draft"]` | 2 |
| `call_caps["get_diff"]` | 0 (no diff access) |
| `call_caps["get_file"]` | 0 (no file access) |

## 9. Model

`LLM_HEIMDALL_*` with `LLM_DEFAULT_*` fallback, via `load_ai_config()` (unchanged).

## 10. What does not change

- The outbox path, approval hash and GitHub write rules.
- `DeskResult`, `SUBMIT_RESULT`, `desk_result_problems` in `schemas.py`.
- `render_desk_result` in `_message.py`.
- The six skill files in `agents/heimdall/skills/`.
- `REQUIRED_SKILLS`, `load_ai_config`, `load_switch_config` in `config.py`.

## 11. Acceptance criteria

| # | Criterion |
| --- | --- |
| AC-1 | On seeded PRs, Heimdall's card `submit_result` cites only `card_id` values saved by this run's `save_card`. |
| AC-2 | A third `save_draft` call in one command is refused with `error:`. |
| AC-3 | A `maintainer.command` from a non-maintainer login is refused before the agent loop starts. |
| AC-4 | `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright` and `uv run pytest tests/unit` pass. |
| AC-5 | No new dependency. |

## 12. Test plan

### Unit — `tests/unit/agents/test_heimdall_runtime.py`

No DB, no network. Use `ScriptedAi` and fake tool handlers.

| Test | Verifies |
| --- | --- |
| `test_card_run_saves_card_and_submits_it` | A scripted model calls `save_card` and `submit_result(action="card", card_id=…)`; run completes as `Completed`. |
| `test_draft_run_saves_draft_and_submits_it` | A scripted model calls `save_draft` and `submit_result(action="draft", draft_id=…)`; run completes. |
| `test_third_draft_is_refused` | Third `save_draft` returns `error:` from the budget cap; model submits without a third draft. |
| `test_submit_with_unsaved_card_id_gets_repair_round` | `submit_result` with an unknown `card_id` triggers one repair round; corrected call completes. |
| `test_explanation_needs_no_saved_id` | `submit_result(action="explanation", explanation="…")` completes without saving anything. |

### Unit — `tests/unit/agents/test_heimdall_guardrails.py`

| Test | Verifies |
| --- | --- |
| `test_non_maintainer_command_is_refused` | `CommandGuard.check` returns a non-None refusal for a login not in the maintainer list. |
| `test_maintainer_command_is_allowed` | `CommandGuard.check` returns `None` for a listed maintainer. |

### Unit — `tests/unit/agents/test_heimdall_context.py`

| Test | Verifies |
| --- | --- |
| `test_build_messages_for_card` | Messages contain the brief in a `pr_data` wrapper and no command text. |
| `test_build_messages_for_command` | Messages contain the brief plus the command text. |
| `test_brief_wraps_pr_text_as_data` | PR title and body appear inside `<pr_data …>` tags. |

## 13. Security notes

- PR content (title, body, findings text, repro output) is wrapped in `pr_data` by the runtime
  and by `context.py`; the model receives it as data, never as instructions.
- Heimdall has no diff or file tool, so it cannot be led to read arbitrary repository content.
- The maintainer check happens before any model call; a non-maintainer cannot trigger a run.
- The approval hash is computed by the desk module from the saved draft body; the agent never
  computes or posts it directly.
