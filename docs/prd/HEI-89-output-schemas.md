# PRD: HEI-89 — Output schemas per agent and room-message renderers

| Field | Value |
| --- | --- |
| Issue | HEI-89 |
| Parent | HEI-83 — Make Huginn, Loki and Heimdall real tool-using agents |
| Blocks | HEI-91 (Huginn), HEI-92 (Loki), HEI-93 (Heimdall), HEI-94 (conversations) |
| Suggested branch | `priyankarpal/hei-89-5-output-schemas-per-agent-and-room-message-renderers` |
| Status | Implemented |
| Design reference | `Heimdall agents agentic design.md`, step 5 |

## 1. Summary

Each agent ends a run by calling its own `submit_*` tool. The tool's JSON schema comes from a
pydantic model in `agents/<name>/schemas.py`. The model is the whole output contract: extra
fields, wrong literals and over-limit text are rejected before anything is rendered or saved.
The room message is then built from the validated object, never from the model's free-text reply.

## 2. Problem

The fixed flows decide the message in code, so the model only fills a narrow slot. Once the model
chooses its own steps (HEI-91 to HEI-93), the only thing keeping its answer bounded is the submit
schema. Without one, an agent could return unpriced prose, cite runs or findings that do not
exist, or claim paired counts the saved runs contradict.

## 3. Models

All three are `extra="forbid"` and frozen, and each agent exports its `SubmitTool` so the runtime
gets the schema from the same model it validates against.

### `agents/huginn/schemas.py`

| Field | Type | Limit |
| --- | --- | --- |
| `verdict` | `reproduced \| not_covered \| setup_failed \| inconclusive` | required |
| `test_cmd` | str | required |
| `repro_ids` | tuple of run ids | at most 3 |
| `with_fix`, `without_fix` | `ModeResult` (`passed`, `failed`, `errors`, each `>= 0`) | required |
| `failing_assertion` | str | at most 300 characters |
| `summary` | str | 1 to 400 characters |

`ModeResult` counts **runs**, not tests: `passed` runs passed the configured command, `failed`
runs ran it and failed, `errors` runs left no saved outcome. A mode's counters must add up to
`len(repro_ids)`, because they describe exactly the runs the verdict names.

`verdict_problems(tools, installation_id, verdict, head_sha)` then checks the submission against
`get_repro`: every named run must exist for this PR head, and both counter sets must equal what
the saved outcomes add up to.

### `agents/loki/schemas.py`

| Field | Type | Limit |
| --- | --- | --- |
| `finding_ids` | tuple of ids | at most 5 (`MAX_FINDINGS`) |
| `skipped_reason` | `docs_only \| no_findings \| null` | optional |
| `summary` | str | 1 to 400 characters |

A review reports saved findings or a skip reason, never both and never neither.

`review_problems(review, saved_finding_ids)` refuses any id `save_findings` did not accept during
this run, which is the code's answer to "exists for the current head".

### `agents/heimdall/schemas.py`

| Field | Type | Limit |
| --- | --- | --- |
| `action` | `card \| draft \| mechanical_task \| explanation` | required |
| `card_id` / `draft_id` / `task_id` / `explanation` | str | exactly the field the action names; explanation at most 800 characters |

`desk_result_problems(result, saved_ids)` refuses an id Heimdall's tools did not return during
this run. An explanation has nothing to check.

## 4. Design decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Where limits live | Pydantic `Field` constraints | One definition feeds both the provider schema and validation, so they cannot drift. |
| Cross-checks | Functions beside the schema, called after validation | They need tool results, which a pydantic validator cannot have; returning problem lines lets the caller reuse the runtime's repair round. |
| Count semantics | Per-run counters derived from `get_repro` | The stored outcome has `passed` plus failures, not test counts, so run counters are the only figure both sides can agree on. |
| Id checks | Compare against what this run's tools returned | Loki and Heimdall have no "read by id" tool; a saved id for the head is exactly what the tool answered. |
| Renderers | `agents/<name>/_message.py`, one function per agent | The message is composed from validated fields only; the model's assistant text is never posted. |
| Model prose in the room | Summary and explanation are posted, tone-filtered, and collapsed to one line where it is a caption | They are bounded validated fields, and the shared tone filter and secret scan already run over a submission. |
| Empty after filtering | Heimdall says it has nothing to add | A filtered-out explanation must not leave a bare `Heimdall:` line. |

## 5. Renderers

| Agent | Function | Message |
| --- | --- | --- |
| Huginn | `render_verdict(verdict)` | verdict label, command, run count, both counter sets, one failing assertion, summary |
| Loki | `render_review(review, *, head_sha, rejected=0)` | `Loki: reviewed <short head>.` plus the saved count, the skip reason or the singular noun, then the summary |
| Heimdall | `render_desk_result(result, saved_message=None)` | the explanation for `explanation`; otherwise the trusted approval message or task block passed in |

## 6. Security

- Every field is bounded, so a submission cannot grow the room post without limit.
- Ids are checked against what the tenant-scoped tools returned, so a model cannot point at
  another installation's run, finding, card or draft.
- Huginn's runs are also checked against the PR head, so a stale run cannot support a verdict.
- Prose fields opt in to `ToneFiltered`, so the shared filter cleans them like every other
  payload; the runtime's secret scan already runs over the serialised submission.
- Ids, counts and verdicts are the only thing rendered for Loki and Huginn; the model's assistant
  reply is never posted.

## 7. Acceptance criteria

- [x] Each model rejects extra fields, wrong literals and values over its limits.
- [x] `ModeResult` counters must account for every cited run.
- [x] Loki's review needs findings or a skip reason, not both and not neither.
- [x] `DeskResult` requires exactly the field its action names.
- [x] A count mismatch with `get_repro` is reported.
- [x] An id that does not exist for the head is reported.
- [x] Each renderer produces the room message from a validated object.
- [x] `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright` and
      `uv run pytest tests/unit` pass.
- [x] No new dependency.

## 8. Test plan

`tests/unit/agents/test_huginn_schemas.py`, `test_loki_schemas.py` and
`test_heimdall_schemas.py`: schema limits and literals, the matching-field rule, the tone filter,
each cross-check, and each renderer's exact text. No database, no network.

## 9. Out of scope

- Wiring the agents onto the runtime and calling these tools (HEI-91 to HEI-93).
- Ask/act conversations (HEI-94).
- Changing how Huginn, Loki or Heimdall behave today; their fixed flows keep their current
  messages until those steps land.
