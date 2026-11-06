# Configuration

**Audience:** operators and maintainers.
**Purpose:** every knob Heimdall reads — `heimdall.yml` layering and schema, plus the `HEIMDALL_*`
environment variables grouped by concern.

## `heimdall.yml` — three layers, later overrides earlier

Heimdall reads configuration from three optional layers and validates the merged result:

1. Built-in defaults.
2. `.github/heimdall.yml` in the owner's `.github` repository, read from that repository's
   default branch (that repo has no copy of the PR's commit).
3. `heimdall.yml` in the pull request repository, read at the PR head commit.

Later layers override earlier values. **Maps merge key by key; lists and scalar values replace the
earlier value** — so a repo can shorten a list the org set. Unknown keys are errors (with a "did
you mean" hint). A missing file is skipped; no files at all gives the defaults.

`load_config` fetches both files every time (a push can edit `heimdall.yml`), compares their
fingerprint (`config_sha`, a SHA-256 of each file's source and text) with the one stored on the
`repos` row, and returns the cached config when they match. An invalid config raises and stores
nothing, so the error shows on every check.

### Full example

```yaml
version: 1
maintainers: []
intake:
  require_linked_issue: true
  require_assignee: true
  required_labels: []
  max_open_prs_per_author: 5
  require_cla: false
  require_ci_green: true
repro:
  test_cmd: "pytest -q"
  allowed_runners: [pytest]
  source_paths: [src/]
  workflow: heimdall-repro.yml
review:
  max_findings: 5
  skip_paths: ["docs/**", "*.md"]
auto_suggest:
  enabled: true
  categories: [lint, typo, rename, missing_import, docstring, changelog]
room:
  switch_room: triage
```

### Per-section meaning

- `maintainers` — GitHub logins allowed to command the Desk (`/heimdall approve`, revise, fix,
  retry, pause auto). Defaults to empty, so Desk commands are disabled until a repo names them.
- `intake.require_linked_issue` — the PR must link an issue.
- `intake.require_assignee` — the PR must have an assignee.
- `intake.required_labels` — labels that must be present.
- `intake.max_open_prs_per_author` — cap on open PRs per author.
- `intake.require_cla` — when true, CLA status comes from PR labels `cla-signed`, `cla: yes`, or
  `cla: signed`.
- `intake.require_ci_green` — CI must be green.
- `repro.test_cmd` — the command Huginn runs. Must start with one of `repro.allowed_runners`;
  Huginn may narrow the command, never replace the runner.
- `repro.allowed_runners` — which runners are permitted (e.g. `pytest`).
- `repro.source_paths` — paths the reproduction concerns.
- `repro.workflow` — the GitHub Actions workflow file used for reproduction.
- `review.max_findings` — Loki's finding cap; cannot exceed 5.
- `review.skip_paths` — globs Loki skips (default `docs/**`, `*.md`).
- `auto_suggest.enabled` — whether mechanical findings can be approved by policy.
- `auto_suggest.categories` — categories eligible for policy approval.
- `room.switch_room` — the default triage room name.

### Errors

Every problem raises `ConfigInvalid` from `shared.errors`, with all of a file's problems in one
message, naming the file:

```
heimdall.yml is invalid:
- intake.max_open_prs_per_author: must be at least 1
- review.max_findngs: unknown key (did you mean 'max_findings'?)
```

Rules that span fields are checked after merging, labelled `effective config (defaults <
.github/heimdall.yml < heimdall.yml)`. Intake shows this text as-is when a config is invalid.

For the schema, defaults, and validation errors, see
`src/heimdall/modules/tenancy/README.md`.

## Environment variables (`HEIMDALL_*`)

Process settings are read from `HEIMDALL_*` environment variables and an optional `.env` file by
`src/heimdall/shared/settings.py`. Required values have no default, so a missing one fails at
startup, not mid-request.

### Required (no default)

| Variable | Meaning |
|---|---|
| `HEIMDALL_DATABASE_URL` | Postgres DSN (secret). |
| `HEIMDALL_GITHUB_APP_ID` | Numeric id of the Heimdall GitHub App. |
| `HEIMDALL_GITHUB_PRIVATE_KEY_PATH` | Path to the mounted App private key (`.pem`). |
| `HEIMDALL_GITHUB_WEBHOOK_SECRET` | Shared secret for verifying webhook signatures (secret). |

### Optional (with defaults)

| Variable | Default | Meaning |
|---|---|---|
| `HEIMDALL_SWITCH_URL` | `None` | Base URL of the Switch server (`http://switch:8000` inside Compose). |
| `HEIMDALL_SWITCH_TOKEN` | `None` | Bot token for Switch (secret). |
| `HEIMDALL_DEFAULT_SWITCH_ROOM_ID` | `None` | Mattermost channel newly enabled repos post to (the bot's room). |
| `HEIMDALL_DEFAULT_SWITCH_AGENT_ROOM_ID` | `None` | Switch room UUID their agents post to — **not** the same id as the channel id. |
| `HEIMDALL_LOG_LEVEL` | `INFO` | Minimum logged level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `HEIMDALL_BRIDGE_WEBHOOK_URL` | `None` | Base URL of the bridge's slash-command/action webhook for rich card buttons; unset means plain markdown replies. |

### LLM configuration (per-agent, falls back to default)

| Variable | Fallback | Meaning |
|---|---|---|
| `LLM_<AGENT>_PROVIDER` | `LLM_DEFAULT_PROVIDER` | Model provider (`openai_compat`, `anthropic`). |
| `LLM_<AGENT>_BASE_URL` | `LLM_DEFAULT_BASE_URL` | Provider base URL. |
| `LLM_<AGENT>_API_KEY` | `LLM_DEFAULT_API_KEY` | API key (secret). |
| `LLM_<AGENT>_MODEL` | `LLM_DEFAULT_MODEL` | Model name. |

One `LLM_DEFAULT_*` block serves all three agents. To override one agent, set `LLM_<AGENT>_*`
(e.g. `LLM_LOKI_BASE_URL`, `LLM_LOKI_MODEL` move only Loki). Each agent builds its own
`AiAdapter` from `agents/<name>/config.py` and calls `ai.chat(...)` — never a vendor SDK.

### Switch setup block (read by `apps.bridge.setup`, not by `Settings`)

| Variable | Meaning |
|---|---|
| `AGENT_REGISTRATION_TOKEN` | Token for registering agents with Switch (secret). |
| `SECRET_KEYS` | `<id>:<secret>` pairs, each secret >= 32 characters (secret). |
| `GATEWAY_ADMIN_EMAIL` / `GATEWAY_ADMIN_PASSWORD` | Switch gateway admin (secret). |
| `MATTERMOST_ADMIN_USER` / `MATTERMOST_ADMIN_PASSWORD` | Mattermost admin (secret). |
| `MATTERMOST_USER` / `MATTERMOST_USER_PASSWORD` | Mattermost maintainer user (secret). |

See `docs/setup.md` and `docs/switch.md` for how these are filled in.

### Secrets

`.env` and the GitHub App private key (`secrets/github-app.pem`) are local secrets. Keep them out
of commits and out of logs. Never log tokens, keys, private-key paths, or full PR diffs; log ids
only.