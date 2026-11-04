# Switch server

`docker compose up` runs a Switch server next to the existing Mattermost, so the agents have
something to register with. There is one Mattermost (the `mattermost` service) and one Postgres
(the `postgres` service, which also holds the `switch` database).

| Service | Purpose | Host port |
|---|---|---|
| `switch` | Switch API. `GET /health` returns `{"status":"ok"}`. | `8000` (`SWITCH_API_PORT`) |
| `gateway` | Switch admin UI. | `3000` (`SWITCH_GATEWAY_PORT`) |
| `switch-db-init` | One-shot: creates the `switch` database and roles. Safe on every `up`. | none |
| `switch-setup` | One-shot: creates the Mattermost admin, team and user, and connects them to Switch. | none |

Heimdall's own API moved to host port `8001` (`API_PORT`) because Switch needs `8000`.

## Setup

1. Copy the template: `cp .env.example .env`.
2. Fill the Switch block of `.env`:

   ```bash
   AGENT_REGISTRATION_TOKEN=$(openssl rand -hex 32)
   SECRET_KEYS=k1:$(openssl rand -hex 32)       # <id>:<secret>, each secret >= 32 characters
   GATEWAY_ADMIN_EMAIL=you@example.com
   GATEWAY_ADMIN_PASSWORD=...
   MATTERMOST_ADMIN_USER=mmadmin
   MATTERMOST_ADMIN_PASSWORD=...                # Mattermost enforces its password rules
   MATTERMOST_USER=maintainer
   MATTERMOST_USER_PASSWORD=...
   ```

   Write the real values in `.env` only. Never commit it.

3. Start the stack: `docker compose up -d`.
4. Check it: `curl http://localhost:8000/health` returns `{"status":"ok"}`, and
   `http://localhost:3000` shows the admin UI. Log in with `GATEWAY_ADMIN_EMAIL` and
   `GATEWAY_ADMIN_PASSWORD`.
5. `docker compose logs switch-setup` ends with `Setup complete!`. Mattermost at
   `http://localhost:8065` now has the `heimdall` team (`MATTERMOST_TEAM_NAME`, the URL slug, not a
   display name) and the `MATTERMOST_USER` account.

`HEIMDALL_SWITCH_URL` and `HEIMDALL_SWITCH_TOKEN` are unchanged and still describe the Mattermost
connection the current bridge uses. The agent adapter (HEI-81) builds on this server.

## Registering the agents

Huginn, Loki and Heimdall each need their own Switch identity. Registration is a one-off step
after the stack is up. Switch shows each API key once, so the command writes ids and keys
straight to `secrets/switch-agents.env` (gitignored, owner-only) and never prints them.

1. **Register the agents**, from the repo root with the values from `.env` exported:

   ```bash
   export SWITCH_API_URL=http://localhost:8000      # your SWITCH_API_PORT
   export AGENT_REGISTRATION_TOKEN GATEWAY_ADMIN_EMAIL GATEWAY_ADMIN_PASSWORD
   uv run python -m apps.bridge.register_agents
   ```

   Switch creates a Mattermost bot for each agent and adds it to the team's default channels
   (`town-square`, `off-topic`). Until addressing is set, only an agent's owner can mention it.
2. **Create the triage room by hand.** In Mattermost, create a channel named `triage` in the
   `heimdall` team and add the three bots (`huginn`, `loki`, `heimdall`) to it. Within a few
   seconds Switch bridges it as a room named `Mattermost: triage`.
3. **Find the Switch room id.** It is Switch's own UUID, **not** the Mattermost channel id:

   ```bash
   docker compose exec postgres psql -U heimdall -d switch -Atc \
     "select id from rooms where name = 'Mattermost: triage'"
   ```
4. **Restrict who may mention the agents** to people in that room. This signs in once as the
   Gateway admin and rotates no key:

   ```bash
   uv run python -m apps.bridge.register_agents --addressing-only --room-id <switch room id>
   ```

   No other agent, and not the Switch platform, may address them. (Passing `--room-id` in step 1
   does the same if the room already exists.)
5. **Check it.** The three agents appear in the Gateway (`http://localhost:3000`). In `#triage`,
   post `@loki hello`. Until an agent's connection is running, Switch answers on its behalf that
   it is offline, which shows the mention was accepted. A `docker compose logs switch` line
   `Addressing denied … not permitted in room` means the room id is wrong.

## Two room ids

The triage room has two ids and they are not interchangeable:

| Id | Looks like | Used by | Where it is stored |
|---|---|---|---|
| Mattermost channel id | 26 letters and digits | the Mattermost bot (checklist, alerts) | `HEIMDALL_DEFAULT_SWITCH_ROOM_ID`, `repos.switch_room_id` |
| Switch room id | UUID (step 3 above) | the agents (Huginn, Loki, Heimdall) | `HEIMDALL_DEFAULT_SWITCH_AGENT_ROOM_ID`, `repos.switch_agent_room_id` |

Switch answers `404` when an agent posts to the Mattermost id, and the post is not retried. Set
both defaults in `.env`, or link one repo with
`python -m apps.admin.link_room <installation id> <repo id> <channel id> --agent-room-id <uuid>`.
A repo with no agent room gets no agent posts (Huginn's verdict, Loki's summary, Heimdall's card).

| Variable | Set by | Meaning |
|---|---|---|
| `SWITCH_<AGENT>_AGENT_ID` | the command | Switch id of `HUGINN`, `LOKI` or `HEIMDALL`. |
| `SWITCH_<AGENT>_API_KEY` | the command | That agent's bearer key. Secret. |
| `SWITCH_API_URL` | you | Switch API root; `http://switch:8000` inside compose. |

**Re-running.** An agent that already exists is skipped with a message and the command exits
1; the others still register. `--overwrite` replaces existing agents, which **rotates their
keys and invalidates the old ones**, so any running agent must be restarted with the new key.

**Retrying addressing.** If registration worked but the addressing step failed (wrong room id,
admin sign-in refused, Switch down), run the command again with `--addressing-only --room-id
<id>`. It reads the agent ids from the secrets file, registers nothing and rotates no key.

**Safety checks.** The command checks the secrets file is writable, and signs in as admin when
`--room-id` is given, *before* asking Switch for any key. If a key still cannot be written, it
says so, stops, and never prints the key; Switch shows a key only once, so fix the file and
re-run with `--overwrite`. `SWITCH_API_URL` must be `https`, except for this machine or a
compose service name such as `http://switch:8000`, and must not contain credentials.

**Compose wiring.** Only the `worker` service loads `secrets/switch-agents.env` (when it exists)
and gets `SWITCH_API_URL=http://switch:8000`, so a compromised api or mcp container does not hold
the agents' keys. The worker uses the keys to post each agent's outbox rows
under that agent's name (routing rules: `src/heimdall/adapters/switch/README.md`).
Restart the worker after registering or rotating keys. With a key missing for an agent, rows that
name it die and the room is alerted; rows that name no agent still use the Mattermost bot.

## Versions

`SWITCH_VERSION` pins the `switch-core`, `gateway` and `setup` images (`ghcr.io/sandbox-quantum`).
List releases with `oras repo tags ghcr.io/sandbox-quantum/standalone-compose`, or query the
registry's `/v2/sandbox-quantum/standalone-compose/tags/list`. Use a released tag, not a
`-rc` one, and bump it deliberately.

## Things that bite

- **`ID_SERVER_NAME` cannot change** after the server has first run without orphaning what it
  created. Decide it before the first `up`.
- **Admin credentials are read once**, on the server's first start. Changing
  `GATEWAY_ADMIN_PASSWORD` later does not change the existing admin.
- **Port 8000 clash:** if something else holds host port 8000, set `SWITCH_API_PORT` and update
  `GATEWAY_PUBLIC_URL` to match. Local Postgres on 5432 is a separate clash; set `POSTGRES_PORT`.
- **Empty secrets:** with `AGENT_REGISTRATION_TOKEN` or `SECRET_KEYS` unset, `switch` fails to
  start. Check `docker compose logs switch`.
- **Starting over:** `docker compose down -v` deletes the Postgres and Mattermost volumes,
  including Heimdall's data.
- **Mattermost version:** Switch's own stack bundles Mattermost 11.9; this repo runs the version in
  `docker-compose.yml`. Switch's Mattermost connection worked against it in testing; treat a
  Mattermost bump as a change to re-verify.
