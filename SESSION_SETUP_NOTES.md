# Switch, Mattermost, and GitHub Setup Session

Date: 2026-11-04

This note records the setup and troubleshooting completed in this session. Secret values are
intentionally omitted. Do not add the Smee channel URL, passwords, webhook secret, GitHub private
key, or Switch agent API keys to this file.

## Intended setup

- Mattermost hosts the `triage` channel.
- Switch connects Mattermost rooms to registered agents.
- Heimdall receives GitHub App webhooks and runs its PR intake, review, reproduction, and approval
  pipeline.
- The user selected `nornslabs/swaitchai-fastapi` as the repository to link to the triage room.
- The user also asked for both general codebase Q&A and PR workflow commands through agent
  mentions. The persistent Switch chat runtime is not wired yet; see **Remaining work**.

## Identifiers

| Item | Value |
|---|---|
| GitHub organization | `nornslabs` |
| GitHub App | `heimdall-gh` |
| GitHub App installation ID | `178321198` |
| Final selected repository | `nornslabs/swaitchai-fastapi` |
| Switch triage room ID | `c4e74da2-abb0-497c-990e-5939a94e86af` |

Switch agent IDs (identifiers only; their API keys are in the private credentials file):

| Agent | Switch agent ID |
|---|---|
| Huginn | `eff8b150-f1ee-4215-90ce-13bb328bf732` |
| Loki | `16480552-4969-493d-b91c-ab6550bd8d55` |
| Heimdall | `b03cb70b-e898-402e-a9c4-4f9040672ee0` |

The agent registration command saved their API keys to `secrets/switch-agents.env`. Do not print,
copy, commit, or share that file.

## Completed Switch and Mattermost setup

- The Switch and Mattermost Compose services were running in the browser/local stack.
- Huginn, Loki, and Heimdall were registered successfully with Switch.
- Addressing for all three agents was restricted to the triage room using
  `register_agents --addressing-only`.
- Mattermost log warnings about a missing user access token were consistent with a stale browser
  session; signing out and back in or clearing Mattermost site data is the browser-side fix.
- Mattermost warnings saying a user was already a channel member were harmless duplicate-add
  notices.
- `mcp` exited with status 0 and restarted because its transport runs over stdio. It is not the
  persistent Switch chat connection.

## GitHub App and webhook troubleshooting

- A GitHub App named `heimdall-gh` was created under the `nornslabs` organization and installed
  with installation ID `178321198`.
- The app webhook was configured to use a Smee channel. Its URL is deliberately not recorded here.
- The GitHub App event subscriptions include Pull request; Workflow run was also enabled. GitHub
  Apps receive `installation` and `installation_repositories` events automatically while the app
  webhook is active.
- The local `.env` was sourced by the user. This agent did not read or edit `.env`, private keys,
  or secret values.
- The first room-link attempt failed because the host-side database password did not match the
  Postgres role. The user adjusted local password settings; a later command reached the tenant and
  repository lookup successfully.
- GitHub’s first `installation_repositories.added` event contained only
  `nornslabs/swaitchai-fastapi`. It did not contain `nornslabs/heimdall`, so the user chose the
  FastAPI repository instead.
- The latest event output showed a processed `installation_repositories.added` event for
  `nornslabs/swaitchai-fastapi` and a processed `installation.new_permissions_accepted` event.
- An earlier query of `repos` returned zero rows before the later `installation_repositories.added`
  event was redelivered. The repo table has not yet been rechecked after that event.

## Code changes in this workspace

The room-link CLI previously required a numeric GitHub repository ID. It now accepts
`owner/repo`, resolves that repository within the specified installation, and then links it to
the Switch room. The README, tenancy README, and CLI argument test were updated accordingly.

The command to link the selected repository is:

```bash
set -a
source .env
set +a
uv run python -m apps.admin.link_room 178321198 nornslabs/swaitchai-fastapi c4e74da2-abb0-497c-990e-5939a94e86af
```

The user has not yet pasted a successful output from this command, so the repo-to-room link is
**not confirmed**. If it reports that the repository is not registered, first check that the
installation event created the repo row:

```bash
docker compose exec postgres psql -U heimdall -d heimdall -c \
  "select installation_id, full_name, enabled, switch_room_id from repos where installation_id = 178321198;"
```

`git diff --check` passed after the code/documentation updates. Tests were not run.

## Remaining work

1. Confirm that `nornslabs/swaitchai-fastapi` appears in the `repos` query above.
2. Run the `link_room` command and confirm it prints a successful link message.
3. Verify the local `.env` has the required GitHub App ID, webhook secret, Smee URL, and default
   Switch room ID. Keep those values local and private.
4. Confirm the GitHub App webhook reaches the running tunnel, API, and worker, then test with a PR
   event for `nornslabs/swaitchai-fastapi`.
5. Implement and start the persistent Switch chat runtime. It must provide read-only codebase
   Q&A and route explicit PR requests through tenant-scoped workflows and existing approval
   safeguards. Registration and room addressing alone do not make the agents answer mentions.
