# HEI-91: Huginn on the agentic runtime

## What changed

Huginn no longer follows a fixed start-three-runs-and-poll loop. It runs on the shared runtime
(`agents/_runtime.py`): the model chooses its steps, the ToolBox and budgets decide what it may
do, and `submit_verdict` is the only way to finish.

| Part | Where |
|---|---|
| Context plan: brief, changed test files, then the source they cover | `agents/huginn/context.py` |
| Own guardrails: allowed runner, only the command changes, three runs | `agents/huginn/guardrails.py` |
| Tools: `get_pr_context`, `get_diff`, `get_file`, `start_repro`, `get_repro`, `recall`, plus `load_skill` and `submit_verdict` | `agents/huginn/allowed_tools.py`, `apps/mcp/main.py` |
| Prompt with the skill index appended by the runtime | `agents/huginn/prompt.md` |
| Limits: `MAX_TURNS = 12`, 24k context, 4 file reads, 40 KB per `get_diff`, 3 runs | `agents/huginn/config.py` |
| Output: `HuginnVerdict`, cross-checked against `get_repro` | `agents/huginn/schemas.py` |

The fixed flow in `agent.py` is gone, and with it `MAX_POLLS`, the majority-vote verdict and the
five recorded replay fixtures under `tests/fixtures/huginn/` that only that flow consumed.

## Judgement calls

1. **The runner rule is about identity, not membership.** The first run may use any runner in
   `allowed_runners`; every later run must use the same one. Without that, narrowing the command
   is allowed but swapping pytest for `npm test` mid-run is not.
2. **"No run" is a real verdict.** A repository with no usable reproduction command submits
   `setup_failed` with no `repro_ids` and zero counters. The schema already requires both mode
   counts to equal the number of named runs, so zero runs means zero counts.
3. **The cross-check is part of the submit tool.** `SubmitTool` gained an optional async
   `cross_check` that runs after schema validation. A verdict whose counts contradict the saved
   runs gets the same one repair round as a schema error instead of being silently dropped.
4. **The brief carries paths, not content.** `context.py` puts the PR text and both file lists
   inside a `pr_data` wrapper; diffs and files stay behind `get_diff` and `get_file`, where the
   runtime applies the byte and context caps.
5. **`recall` stays in the allowlist.** HEI-90 gave every agent its notes tool; HEI-91's tool list
   adds the read tools Huginn needs without taking that away.

## Acceptance

- On the three seeded PRs (`good_fix`, `fake_fix`, `rule_breaker`), a scripted model drives the
  runtime and Huginn submits `reproduced`, `not_covered` and a `setup_failed` verdict with no
  run, in that order.
- A fourth `start_repro` is refused and only three runs exist.
- A runner change between runs is refused and only the first run exists.
- A verdict that disagrees with the saved runs gets one repair round; a second refusal ends the
  run without a post.
