# PRD: HEI-88 — Skills loader, `load_skill` tool, and skill files

| Field | Value |
| --- | --- |
| Issue | HEI-88 |
| Parent | HEI-83 — Make Huginn, Loki and Heimdall real tool-using agents |
| Depends on | HEI-84 — Agent runtime: ToolBox, context budgets, submit with repair loop |
| Blocks | HEI-91, HEI-92, HEI-93 |
| Suggested branch | `priyankarpal/hei-88-4-skills-loader-load_skill-tool-and-skill-files-for-all` |
| Status | Draft for implementation |
| Design reference | `Heimdall agents agentic design.md`, step 4 |

## 1. Summary

Add a small, table-driven skills system to the agents:

1. A loader in `agents/_skills.py` that reads `agents/<name>/skills/*.md` and validates each
   playbook's frontmatter at startup.
2. A local `load_skill(name)` tool that returns one playbook's text to the model.
3. Thirteen markdown skill files: three for Huginn, four for Loki and six for Heimdall.

The system prompt stays small. It lists each skill's name and one-line description only. The
agent loads the full playbook on demand when the task matches.

## 2. Problem

Today every agent's behaviour lives in one long `prompt.md`. Improving one behaviour, such as how
Huginn judges a setup failure, means editing the shared prompt and risking every other behaviour.
The prompt is also sent in full on every run, including playbooks the current task does not need.

As the agents move onto the shared runtime in HEI-91 to HEI-93, they will need a compact way to
discover the right procedure at the right moment without growing the base prompt.

## 3. Goals

- Keep the base system prompt small: one index line per skill, no playbook text.
- Let an agent load exactly one playbook by name with a local tool.
- Make a bad skill fail at startup, never in the middle of a PR run.
- Keep skills provider-agnostic: plain markdown, no code, no vendor SDK.
- Make each playbook independently reviewable and editable.
- Enforce the existing per-agent tool allow-list on a skill's declared tools.

## 4. Non-goals

- Moving Huginn, Loki or Heimdall onto the shared runtime. That is HEI-91, HEI-92 and HEI-93.
- Adding `get_file`, `get_thread`, `recall`, `remember` or `forget`. Those belong to later issues.
- A remote skill registry, database storage, versioning, hot reload or skills authored by a model.
- Executing code from a skill. Skills are text only.
- Changing the approval, outbox, MCP or GitHub-write rules.
- Changing an agent's existing deterministic flow in this issue.
- Adding a new dependency. `pyyaml` is already installed.

## 5. Design decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Where skills live | `agents/<name>/skills/*.md` | Ships with the agent image; reviewable in the repo. |
| Frontmatter parser | YAML between `---` lines, parsed with `pyyaml` | Already a dependency; familiar format. |
| What the prompt shows | `name` and `description` only | Matches the issue and keeps the index small. |
| What `when` is for | Human-readable trigger, validated and used by tests/docs; **not** added to the prompt | The prompt line must stay short. `description` must still tell the model when to load. |
| `load_skill` registration | Runtime-provided local tool for every agent, not listed in `allowed_tools.py` | It is a meta-tool, not an MCP tool. Skills never need to list it. |
| Allow-list source | The agent's existing `ALLOWED_TOOLS` in `agents/<name>/allowed_tools.py` | One source of truth for what an agent may call. |
| Missing required skill | Fails startup | A deleted playbook must be loud, not a silent quality regression. |
| Extra skill files | Allowed and indexed | Adding a playbook is a file-only change. Core required skills stay protected. |
| Startup failure type | `ConfigInvalid` | Non-retryable configuration error, consistent with model/switch config. |
| Body size cap | 8,000 characters per skill | Keeps one `load_skill` result inside the smallest agent context budget. |
| Hot reload | None | Skills load once at process start; restart to pick up a change. |

## 6. Skill file format

### 6.1 Location

```text
agents/
  huginn/skills/
    pick_test_command.md
    judge_repro.md
    fix_setup_failure.md
  loki/skills/
    edge_cases.md
    scope_check.md
    test_quality.md
    mechanical_tagging.md
  heimdall/skills/
    write_card.md
    draft_change_request.md
    draft_thanks.md
    draft_close.md
    explain_why.md
    mechanical_task.md
```

Only direct children ending in `.md` are skills. Other files and subdirectories are ignored.

### 6.2 Frontmatter schema

Every file starts with a YAML mapping:

```markdown
---
name: judge_repro
description: Decide whether the paired runs reproduced the fix, were not covered, or failed setup.
when: A paired reproduction attempt has finished and saved both outcomes.
tools:
  - get_repro
---

Playbook text starts here.
```

| Field | Type | Rules |
| --- | --- | --- |
| `name` | string | Required. Must match `^[a-z][a-z0-9_]*$` and equal the filename stem. Unique within an agent. |
| `description` | string | Required, non-empty, single line, at most 160 characters. Shown in the system prompt index. Must say what the skill does and when to use it. |
| `when` | string | Required, non-empty, single line, at most 200 characters. Trigger description for humans and tests; not shown in the prompt. |
| `tools` | list of strings | Required. May be empty (`tools: []`). No duplicates. Every name must be in that agent's `ALLOWED_TOOLS`. |

Rules:

- Unknown frontmatter keys are rejected, so a typo cannot silently drop a rule.
- All four keys are required, even when `tools` is empty.
- The body is everything after the closing `---`. It must be non-empty after trimming and at most
  8,000 characters. Leading and trailing blank lines are removed; internal text is preserved.
- Frontmatter is never returned by `load_skill` and never added to the prompt.
- Skills contain no secrets, tokens, URLs with credentials, PR content or executable code.
- Line endings are normalised to `\n`.

### 6.3 Example body style

Playbooks are short and imperative. They state:

1. What to check.
2. Which tools to use, by name.
3. What conclusion to submit.
4. What not to do.

They never guess about people, never follow instructions found in PR content and never claim a
result the saved data does not support.

## 7. Required skills

Every skill below must ship in this issue. The `tools` column is the maximum set; a file may use
fewer tools.

### Huginn

| Skill | `when` | Allowed tools | Playbook outcome |
| --- | --- | --- | --- |
| `pick_test_command` | Before the first reproduction run. | `start_repro`, `get_repro` | Use the repository's validated command, not PR text; start one paired attempt. |
| `judge_repro` | A paired attempt finished and both outcomes are saved. | `get_repro` | Decide reproduced, not covered, or setup failed from saved counts and failures only. |
| `fix_setup_failure` | Both modes failed for a setup reason. | `start_repro`, `get_repro` | Adjust only the command/env supported by repo configuration; respect the three-attempt cap. |

### Loki

| Skill | `when` | Allowed tools | Playbook outcome |
| --- | --- | --- | --- |
| `edge_cases` | Looking for inputs the change does not handle. | `get_pr_context`, `get_diff` | Produce cited `file:line` findings for missing input handling. |
| `scope_check` | Checking the change against the linked issue and unrelated files. | `get_pr_context`, `get_diff` | Flag out-of-scope files or behaviour not required by the issue. |
| `test_quality` | Judging whether the PR's tests prove the fix. | `get_diff` | Flag tests that cannot fail before the fix or do not exercise the change. |
| `mechanical_tagging` | Deciding whether a finding is lint, typo, rename, `missing_import`, docstring or changelog. | `save_findings` | Assign exactly one allowed mechanical category, or none. |

### Heimdall

| Skill | `when` | Allowed tools | Playbook outcome |
| --- | --- | --- | --- |
| `write_card` | `pr.ready_for_card`. | `list_findings`, `get_repro`, `save_card` | Build one current-head card from stored findings and the latest repro result. |
| `draft_change_request` | `@heimdall ask changes`. | `list_findings`, `get_repro`, `save_draft` | Draft a specific, polite change request tied to cited findings. |
| `draft_thanks` | `@heimdall thank`. | `save_draft` | Draft a short thank-you that states what was verified. |
| `draft_close` | `@heimdall close`. | `save_draft` | Draft a close message that gives the reason and cites the evidence. |
| `explain_why` | `@heimdall why`. | `list_findings`, `get_repro` | Explain the current card's verdict and gaps from saved data. |
| `mechanical_task` | A finding is in an `auto_suggest` category. | `create_mechanical_task`, `list_findings` | Create one policy-gated task; never post directly. |

## 8. Functional requirements

### FR-1 — Loader entry point

`agents/_skills.py` exposes a loader that takes the agent name, its `ALLOWED_TOOLS` and its
required skill names, and returns an immutable skill set.

```python
skills = load_skills(
    agent=AgentName.HUGINN,
    allowed_tools=ALLOWED_TOOLS,
    required=frozenset({"pick_test_command", "judge_repro", "fix_setup_failure"}),
)
```

The returned value exposes:

- `skills.prompt_index()` — one line per skill, sorted by name, with only `name` and
  `description`.
- `skills.load(name)` — the playbook body, or an error result for an unknown name.
- `skills.tool()` — an `AgentTool` for `load_skill`, ready to pass to `ToolBox`.
- `skills.names()` — discovered names, for logs and tests.

### FR-2 — Discovery

- Read `agents/<agent>/skills/` relative to the package, not the process working directory.
- Load every `*.md` direct child.
- A missing directory, an unreadable file, zero skill files, or a missing required skill is a
  startup failure.
- Duplicate `name` values are a startup failure, even if the filenames differ.

### FR-3 — Frontmatter validation

Reject, with a message naming the file, field and reason:

- missing opening or closing `---`;
- invalid YAML, or a YAML value that is not a mapping;
- an unknown key;
- a missing, empty or non-string `name`, `description` or `when`;
- a `name` that fails the pattern or does not equal the filename stem;
- a `description` over 160 characters, or `when` over 200 characters;
- a `description` or `when` containing a newline;
- `tools` missing, not a list, containing a non-string, or containing duplicates;
- a tool not in `ALLOWED_TOOLS`;
- an empty body or a body over 8,000 characters.

Validation collects every error across the agent's skills and raises one `ConfigInvalid` listing
them sorted by file path. It does not stop at the first file. The message is capped so a badly
broken directory cannot flood the log.

### FR-4 — Startup failure

Each agent's `runner.py` loads and validates its skills before opening the queue or accepting a
Switch connection.

- On success, log `agent_skills_loaded` with the agent name, count and skill names.
- On failure, raise `ConfigInvalid`; the process exits non-zero and the container restarts.
- No job runs against a partially valid skill set.
- Startup does not read PR content, call GitHub, call Switch or call a model.

### FR-5 — Prompt index

`prompt_index()` returns only:

```text
## Skills

- edge_cases: Find inputs the change does not handle.
- mechanical_tagging: Decide whether a finding is a mechanical category.
- ...
```

- Sorted by skill name.
- One line per skill.
- No `when`, body text, file path or allowed-tool list.
- Empty for an agent with no skills, though every shipped agent has required skills.

HEI-91 to HEI-93 append this index to each agent's system message. HEI-88 only provides and tests
the index.

### FR-6 — `load_skill` tool

Tool spec:

| Part | Value |
| --- | --- |
| Name | `load_skill` |
| Description | "Load the full playbook for one skill. Call only when the current task matches a skill description. Returns the playbook text." |
| Input | object with required string property `name`, with `enum` set to the discovered skill names |
| Side effects | none: no network, no database, no writes, no model call |
| Result | the exact playbook body as plain text |

Behaviour:

- A known name returns the body with frontmatter removed and outer blank lines trimmed.
- The `enum` stops an unknown name before the handler runs, so the model gets the existing
  `error: invalid arguments ...` result listing the valid names.
- The handler repeats the lookup for direct calls and returns `error: unknown skill '<name>'`;
  the name is echoed only after it is reduced to the characters a skill name may contain.
- The tool is local and always available to every agent on the runtime. It is not added to
  `ALLOWED_TOOLS` and a skill may not list it in `tools`.
- Repeated calls are allowed. The runtime's context budget still applies.
- The handler logs `agent_skill_loaded` with agent, skill and character count. It never logs the
  body.

### FR-7 — Provider independence

- Skills are plain text. No model SDK, CLI, vendor-specific markup or embedded tool calls.
- The loader and tool use only the standard library, `pyyaml`, pydantic or dataclasses, and the
  existing runtime types.
- Switching model provider changes no skill file.

### FR-8 — Documentation

Update `agents/README.md` with:

- the skill directory layout and frontmatter schema;
- startup validation and failure behaviour;
- how `load_skill` is registered and called;
- how to add a skill safely.

Update each agent's README with its skill list and trigger conditions. When HEI-91 to HEI-93 land,
their README updates record the runtime wiring.

## 9. Edge cases

| Case | Expected behaviour |
| --- | --- |
| `skills/` directory missing | Startup fails with the expected path. |
| Directory exists but has no `.md` files | Startup fails: no skills loaded. |
| Required skill missing | Startup fails and names the missing skill. |
| Extra, non-required skill | Loads and appears in the index. |
| `tools: []` | Valid; the skill only reasons or submits through the agent's normal `submit_*`. |
| Skill lists `load_skill` | Startup fails; the meta-tool is not a skill tool. |
| Skill lists an MCP tool from another agent | Startup fails and names the tool. |
| Duplicate tool names in `tools` | Startup fails. |
| Duplicate skill names in two files | Startup fails. |
| Frontmatter has `tool:` instead of `tools:` | Startup fails on the unknown key and the missing required key. |
| Body contains fenced code | Allowed; it is still text and is never executed. |
| Body contains a secret | Rejected in review, not detectable by code; see security rules. |
| Model asks for an unknown skill | Rejected by the input schema with an error listing the valid names; the run continues. |
| Model loads a skill twice | Allowed; runtime budget decides whether it fits. |
| Skill body is close to the context limit | Runtime truncates the tool result as usual; the playbook cap makes this unlikely. |
| Process cannot read a file | Startup fails with the file path and error type, not file content. |

## 10. Security and privacy

- Skill files are trusted repository content, not PR content. They must never be populated from
  a PR title, body, diff, comment or test output.
- The loader never writes skill text to logs. It logs names and counts only.
- Error messages name the file, field and rule. They do not echo the rejected value.
- `load_skill` has no network, filesystem-write or shell access. It reads an in-memory mapping
  built at startup.
- Skill text cannot grant tools. The ToolBox allow-list and `ALLOWED_TOOLS` still decide what
  runs, and the approval hash still decides what reaches GitHub.
- A skill must not contain tokens, private keys, webhook URLs or credentials. This is checked in
  review, not at runtime.
- The tool's `name` argument is untrusted model input and is matched only against the in-memory
  skill map.

## 11. Observability

| Event | Level | Fields |
| --- | --- | --- |
| `agent_skills_loaded` | info | `agent`, `count`, `skills` (names) |
| `agent_skills_invalid` | error | `agent`, `errors` (count, no bodies) |
| `agent_skill_loaded` | info | `agent`, `skill`, `characters` |

Startup failure is visible as a non-zero exit and a restarting container. No dashboard or alert is
added in this issue.

## 12. Acceptance criteria

- [ ] **AC-1** `agents/_skills.py` loads all thirteen shipped skill files.
- [ ] **AC-2** Every shipped skill's frontmatter passes validation and names only tools in that
      agent's `ALLOWED_TOOLS`.
- [ ] **AC-3** A test skill that names a tool outside the agent's allow-list raises
      `ConfigInvalid` and fails startup.
- [ ] **AC-4** Missing directory, missing required skill, malformed frontmatter and duplicate
      names all raise `ConfigInvalid` before any job runs.
- [ ] **AC-5** `load_skill("judge_repro")` returns the exact playbook body without frontmatter.
- [ ] **AC-6** `load_skill` with an unknown name returns an `error:` result and the run continues.
- [ ] **AC-7** The prompt index contains only names and descriptions, sorted, with no bodies or
      `when` text.
- [ ] **AC-8** Each runner calls startup validation before opening its queue.
- [ ] **AC-9** All three agent READMEs and `agents/README.md` document the format and wiring.
- [ ] **AC-10** `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright` and
      `uv run pytest tests/unit` pass.
- [ ] **AC-11** No new dependency is added.

## 13. Test plan

TDD order: write the failing tests below, then implement.

### Unit — `tests/unit/agents/test_skills.py`

Use `tmp_path` fixture directories; no DB, no network, no model.

- Loads a valid skill and returns its body exactly.
- Strips frontmatter and outer blank lines, preserves internal text.
- Index is sorted and contains only name and description.
- Rejects each frontmatter failure in FR-3, parametrised.
- Rejects a tool outside the allow-list.
- Rejects `load_skill` in a skill's `tools`.
- Rejects a missing required skill.
- Rejects duplicate names across files.
- Rejects an empty directory and a missing directory.
- Rejects a body over the cap and an empty body.
- Ignores non-markdown files and nested directories.
- `tool()` returns a `ToolSpec` named `load_skill` with the discovered names in the input schema.
- The handler returns the body for a known name.
- The handler returns an error for an unknown or unsafe name.
- The handler logs only the skill name and character count.

### Repository invariant — `tests/unit/agents/test_shipped_skills.py`

- Parametrise over `AgentName` and validate the real `agents/<name>/skills/` directory against
  the real `ALLOWED_TOOLS`.
- Assert the exact required names:
  - Huginn: `pick_test_command`, `judge_repro`, `fix_setup_failure`.
  - Loki: `edge_cases`, `scope_check`, `test_quality`, `mechanical_tagging`.
  - Heimdall: `write_card`, `draft_change_request`, `draft_thanks`, `draft_close`, `explain_why`,
    `mechanical_task`.
- Assert every body is non-empty and under the cap.

### Startup

- Calling each runner's startup validation helper with a broken skills directory raises
  `ConfigInvalid` before the queue is opened.
- A valid directory logs `agent_skills_loaded` once and does not call GitHub, Switch or a model.

### Runtime integration (minimal)

- `ToolBox` accepts `skills.tool()` when the allowed set includes `load_skill`.
- A scripted model calls `load_skill`, receives the body, then calls `submit_*`; the run completes.
- The tool result is truncated by the existing context budget if it does not fit.

### Manual

- `docker compose --profile app up` starts all three agents without a skills error.
- Temporarily add a skill naming `get_diff` to Huginn and confirm the Huginn container exits with
  `ConfigInvalid`; remove it and confirm it starts again.
- Ask a scripted or local runtime smoke test to load one skill and confirm the body matches the
  file.

## 14. Rollout

- No database migration and no configuration variable.
- Skill files ship inside the agent image; rebuild and recreate `huginn`, `loki` and `heimdall`.
- If a skill is wrong after deploy, roll back the image or fix the file and redeploy. There is no
  hot reload by design.
- HEI-91 to HEI-93 consume `prompt_index()` and `tool()`; until then, the loader runs at startup
  and its accessors are covered by unit tests.

## 15. Resolved questions

1. **Does `when` appear in the prompt?** No. Only `name` and `description` do. `when` is required
   for authors and tests, and keeps trigger intent explicit without spending prompt tokens.
2. **Is `load_skill` in `allowed_tools.py`?** No. It is a local meta-tool registered by the
   runtime. Skills cannot declare it.
3. **Can a skill exist without being in the required list?** Yes. Extra skills are discovered and
   indexed; required skills are protected against accidental deletion.
4. **What happens when the model asks for a missing skill?** It gets an `error:` tool result. That
   is a live-run input error, not a startup failure.
5. **Where do skills run?** In-process. No MCP server, no network, no separate service.

## 16. Out of scope for follow-up issues

- Appending the skill index to each agent's system prompt and passing `skills.tool()` into its
  `ToolBox` (HEI-91 to HEI-93).
- `get_file`, `get_thread`, `recall`, `remember`, `forget`, and `agent_memory`.
- Skill analytics, A/B testing, version pinning or a skill authoring UI.
