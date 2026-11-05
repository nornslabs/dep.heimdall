# Agent runtime

The shared loop Huginn, Loki and Heimdall run on. The model decides what to do next; the runtime
decides what it may do and what a finished answer looks like. Agents import it from
`agents/_runtime.py` only.

## Contract

```python
run = AgentRun(agent, ai, toolbox, submit, budget, head_sha, read_current_head)
outcome = await run_agent(run, messages)
```

`run_agent` never posts anything. It returns a `RunOutcome`, and the caller turns that into a
room card through the outbox:

| Outcome | When |
|---|---|
| `Completed(payload, usage, turns)` | The model called `submit_*` with a valid payload and the head had not moved |
| `CouldNotFinish(reason, usage, turns)` | `turn_limit`, `token_budget`, or `invalid_submission` (second bad submit) |
| `Unavailable(reason, usage, turns)` | The model (after the `AiAdapter`'s 3 attempts) or a tool (after 3 attempts) stayed down; `reason` is the error type |
| `Stale(usage, turns)` | The PR head moved before the submission; the answer is dropped |

## Rules it enforces

- **ToolBox.** Only the agent's allowed tools are offered. Any other name gets
  `error: tool '<name>' is not allowed` and nothing runs.
- **Arguments.** Checked against the tool's JSON Schema before the call. A bad argument goes
  back to the model as an error and never reaches the tool server.
- **Context budget.** A result that would overflow `context_tokens` (estimated at 4 characters
  per token) or the tool's byte cap is cut on a line boundary and ends with
  `[truncated: N lines, ask for a narrower path]`.
- **Call caps.** Once a capped tool has run its allowed number of times, further calls get an
  error and the model has to finish with what it has.
- **Turns and tokens.** `max_turns` model calls and `run_tokens` (from the provider's `usage`).
- **`submit_*`.** The only way to finish. The payload is validated with pydantic. The first
  failure sends the errors back (field and message, never the submitted values) with one
  reserved extra turn. A second failure ends the run as `invalid_submission`.
- **Plain text replies.** The model is told to finish by calling `submit_*`; requests use
  `tool_choice="required"`.
- **Tool errors.** An error marked `retryable` (an outage) is retried twice with backoff, then the
  run is `Unavailable`. Any other error goes back to the model as `error: <ExceptionType>`.

## Skills system

Each agent loads playbooks from `agents/<name>/skills/*.md` at startup. The system prompt gets only
a compact index (name + description). The model calls the local `load_skill` tool to read one
playbook when its task matches the description.

### Skill file format

Every `.md` file in `agents/<name>/skills/` is a skill. The file must start with YAML frontmatter:

```markdown
---
name: skill_name
description: One line shown in the system prompt; what the skill does and when to use it.
when: Trigger description for authors and tests; not shown to the model.
tools:
  - allowed_tool_1
  - allowed_tool_2
---

Playbook text starts here. It must be non-empty and at most 8,000 characters.
```

Frontmatter rules:
- All four fields are required. `tools` may be an empty list.
- `name` must match `^[a-z][a-z0-9_]*$` and equal the filename stem.
- `description` ≤ 160 chars, single line. `when` ≤ 200 chars, single line.
- Every tool in `tools` must be in that agent's `ALLOWED_TOOLS`.
- Unknown frontmatter keys are rejected.

### Startup validation

Each runner calls `load_skills()` before opening its queue. Validation failures raise
`ConfigInvalid` and exit the process. The container restarts; no job runs against a partially
valid skill set. The log event `agent_skills_loaded` records the agent, count and skill names on
success. `agent_skills_invalid` logs the error count on failure.

### The `load_skill` tool

Registered automatically for every agent as a local tool (not in `ALLOWED_TOOLS`). Its input schema
has an `enum` of the discovered skill names, so an unknown name is rejected before the handler
runs. The handler returns the playbook body (frontmatter removed, outer blank lines trimmed) and
logs `agent_skill_loaded` with the skill name and character count—never the body text.

### Adding a skill safely

1. Create `agents/<name>/skills/<skill_name>.md` with valid frontmatter.
2. Ensure every declared tool is in that agent's `ALLOWED_TOOLS`.
3. Run the agent's unit tests: `uv run pytest tests/unit/agents/test_shipped_skills.py -k <agent>`.
4. Start the agent container to confirm `agent_skills_loaded` appears in the logs.

Extra skills are allowed; only the names in `REQUIRED_SKILLS` are protected against deletion.

## Files

| File | Role |
|---|---|
| `_runtime.py` | `run_agent` and the public names agents import |
| `_turn.py` | One turn: model call, tool calls, submit handling; `ChatModel`, `AgentRun`, `still_current` |
| `_toolbox.py` | `AgentTool`, `ToolBox`: allow-list, JSON-schema checks, outage retries |
| `_budget.py` | `AgentBudget`, `CallCounts`, `fit_to_budget` |
| `_submit.py` | `SubmitTool`: the `submit_*` schema and its error text |
| `_outcome.py` | `RunUsage` and the four outcome types |
| `_skills.py` | Loader, validation, prompt index, `load_skill` tool |
| `_skill_frontmatter.py` | Frontmatter parsing and validation |

Each agent's budget is `BUDGET` in `agents/<name>/config.py`. The numbers are starting points
to tune on the seeded PRs.
