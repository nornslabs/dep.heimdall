# Switch bridge

The bridge streams room events and records maintainer approvals from thumbs-up reactions on
draft posts. Approval posts include a hidden marker with the draft id, exact body hash,
installation id and repository. Mattermost usernames are treated as GitHub logins; deployments
must configure those identities consistently with `heimdall.yml` maintainer entries.

Non-maintainers receive one short thread reply. They create no approval or GitHub outbox entry.

## First-time setup

`python -m apps.bridge.setup [--channel NAME] [--env-file PATH] [--secrets-file PATH]
[--mattermost-url URL] [--print]` runs once after `docker compose up -d` and before the app
profile. It creates the triage channel and a Mattermost access token, registers the agents (only
when they are not already saved), adds the bots to the channel, reads Switch's room UUID and
writes `HEIMDALL_SWITCH_TOKEN`, `HEIMDALL_DEFAULT_SWITCH_ROOM_ID` and
`HEIMDALL_DEFAULT_SWITCH_AGENT_ROOM_ID` to the env file. The token is hidden in the summary unless
`--print` is given. Exit code 0 means all values were written, 1 that a step failed or Switch has
not bridged the channel yet (re-run), 2 that configuration was missing. Safe to re-run.

## Registering the agents

`python -m apps.bridge.register_agents [--room-id ID] [--overwrite | --addressing-only] [--secrets-file PATH]`
registers Huginn, Loki and Heimdall with Switch and saves each agent's id and API key to
`secrets/switch-agents.env` (owner-only, never printed). With `--room-id` it also restricts who
may @-mention each agent to people in that room. Exit code 0 means all three succeeded, 1 that
any was skipped or failed, 2 that configuration was missing or the admin sign-in was refused. `--addressing-only` retries just the addressing step from the saved ids, rotating no key. Setup steps and re-run behaviour:
`docs/switch.md`.
