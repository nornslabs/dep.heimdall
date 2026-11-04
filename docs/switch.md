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
connection the current bridge uses. Agent registration (HEI-80) and the agent adapter (HEI-81)
build on this server.

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
