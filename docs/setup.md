# Setup

**Audience:** contributors and operators.
**Purpose:** go from a fresh clone to a triaged pull request, step by step, without asking anyone.

## Prerequisites

- Docker Compose
- uv (Python `>=3.12,<3.13`, managed by `.python-version`)
- A Heimdall GitHub App installed on the account/organization you want to triage
- A Mattermost/Switch stack for the room

## Ports used by the stack

| Service | Host port | Env var | Purpose |
|---|---|---|---|
| Switch API | `8000` | `SWITCH_API_PORT` | Agent registration and room bridging |
| Switch gateway (admin UI) | `3000` | `SWITCH_GATEWAY_PORT` | Admin UI at `http://localhost:3000` |
| Mattermost | `8065` | — | The chat room agents post into |
| Postgres | `5432` | `POSTGRES_PORT` | Heimdall + Switch databases |
| Heimdall API | `8001` | `API_PORT` | Webhook endpoint (moved off 8000 for Switch) |

Switch needs host port `8000`, so the Heimdall API moved to `8001`. If something else holds port
8000, set `SWITCH_API_PORT` and update `GATEWAY_PUBLIC_URL` to match. Local Postgres on 5432 is a
separate clash; set `POSTGRES_PORT`.

## Step 0 — configure the GitHub App

In GitHub, create or open the Heimdall App and set:

- **Repository permissions:** `metadata: read`, `contents: read`, `pull requests: read/write`,
  `issues: read/write`, `checks: read`, `actions: read/write`
- **Subscribe to events:** `pull_request`, `workflow_run`, `check_suite`, `installation`,
  `installation_repositories`
- **Webhook URL:** point it at a Smee channel (or your bridge's public webhook URL)
- **Webhook secret:** the same value you put in `HEIMDALL_GITHUB_WEBHOOK_SECRET`

Download the App private key to `secrets/github-app.pem`. The Compose stack mounts it read-only at
`/run/secrets/github-app.pem`. Never commit `.env` or `secrets/`.

## Step 1 — copy the env template

```bash
cp .env.example .env
```

Fill in at least:

- `HEIMDALL_GITHUB_APP_ID` — the App's numeric id
- `HEIMDALL_GITHUB_WEBHOOK_SECRET` — matches GitHub
- `HEIMDALL_SWITCH_URL` — `http://switch:8000` inside Compose, `http://localhost:8000` locally
- `SMEE_URL` — your Smee channel URL

Point the agents at a model. One `LLM_DEFAULT_*` block serves all three agents; any provider with an
OpenAI-compatible API works:

```
LLM_DEFAULT_PROVIDER=openai_compat        # openai_compat | anthropic
LLM_DEFAULT_BASE_URL=https://openrouter.ai/api/v1
LLM_DEFAULT_API_KEY=...
LLM_DEFAULT_MODEL=...
```

To override one agent, set `LLM_<AGENT>_*`. For example, `LLM_LOKI_BASE_URL` and `LLM_LOKI_MODEL`
move only Loki to a different model family.

## Step 2 — start infrastructure and run one-off setup

```bash
docker compose up -d
uv run python -m apps.bridge.setup   # add --print to also show the token
```

Setup does, in order, and stops at the first step that cannot finish:

1. Waits for Switch and Mattermost to be healthy.
2. Signs in to Mattermost as `MATTERMOST_ADMIN_USER`, creates (or reuses) the `triage` channel and
   a personal access token -> `HEIMDALL_SWITCH_TOKEN`, `HEIMDALL_DEFAULT_SWITCH_ROOM_ID` (written
   to `.env` immediately, because Mattermost shows a token only once).
3. Registers Huginn, Loki and Heimdall with Switch (skipped when `secrets/switch-agents.env`
   already has all three; no key is ever rotated).
4. Adds the three bots, and `MATTERMOST_USER` if set, to the channel.
5. Waits for Switch to bridge the channel, then reads Switch's own room UUID ->
   `HEIMDALL_DEFAULT_SWITCH_AGENT_ROOM_ID`.
6. Restricts who may mention the agents to people in that room.

It is safe to re-run: a token that still works is kept, an existing channel and registered agents
are reused.

The two room ids are **not interchangeable**:

| Id | Looks like | Used by | Stored in |
|---|---|---|---|
| Mattermost channel id | 26 letters and digits | the Mattermost bot (checklist, alerts) | `HEIMDALL_DEFAULT_SWITCH_ROOM_ID`, `repos.switch_room_id` |
| Switch room id | UUID | the agents (Huginn, Loki, Heimdall) | `HEIMDALL_DEFAULT_SWITCH_AGENT_ROOM_ID`, `repos.switch_agent_room_id` |

Switch answers `404` when an agent posts to the Mattermost id. Set both defaults in `.env`, or link
one repo with `python -m apps.admin.link_room <installation_id> <repo_id> <room_id>
--agent-room-id <uuid>`. A repo with no agent room gets no agent posts.

## Step 3 — migrate the database

```bash
uv run alembic upgrade head
```

Or let Compose run migrations when the app profile starts.

## Step 4 — start the app stack

```bash
docker compose --profile app --profile tunnel up -d --build
```

This adds migrations, the API, worker, MCP server, bridge, the three agents, and the Smee
forwarder. The API listens at `http://localhost:8001`; Mattermost at `http://localhost:8065`; Switch
at `http://localhost:8000` with its admin UI at `http://localhost:3000`. See `docs/switch.md` for
Switch details.

The agents claim linked rooms when they start, so start (or restart) them after the values are in
`.env` and the repo is linked.

## Step 5 — install the App on a repository and triage

Install the GitHub App on a repository with Actions enabled. Open or update a PR and check its
thread in the `triage` room. Completed reproduction workflows return through the `workflow_run`
webhook and their paired result artifact.

To move one repo to a different room later:

```bash
python -m apps.admin.link_room <installation_id> <repo_id> <room_id> --agent-room-id <uuid>
```

## Re-running setup

Setup is idempotent. If step 5 times out, wait a few seconds and run it again. `--overwrite`
replaces existing agents, which **rotates their keys and invalidates the old ones**, so restart the
worker and the agents afterwards.

## Things that bite

- `ID_SERVER_NAME` cannot change after the server's first run without orphaning what it created.
  Decide it before the first `docker compose up`.
- Admin credentials are read once, on the server's first start. Changing `GATEWAY_ADMIN_PASSWORD`
  later does not change the existing admin.
- Empty `AGENT_REGISTRATION_TOKEN` or `SECRET_KEYS` makes `switch` fail to start; check
  `docker compose logs switch`.
- `docker compose down -v` deletes the Postgres and Mattermost volumes, including Heimdall's data.
- Switch's stack bundles Mattermost 11.9; this repo runs the version in `docker-compose.yml`. Treat
  a Mattermost bump as a change to re-verify.

For the full Switch reference (agent registration by hand, addressing, two room ids, versions) see
`docs/switch.md`.