# Switch bridge

The bridge streams room events and records maintainer approvals from thumbs-up reactions on
draft posts. Approval posts include a hidden marker with the draft id, exact body hash,
installation id and repository. Mattermost usernames are treated as GitHub logins; deployments
must configure those identities consistently with `heimdall.yml` maintainer entries.

A maintainer can instead reply `/heimdall approve` in the PR's thread. The bridge fetches the
thread (`RoomPort.get_thread_posts`), picks the newest approval post (or the one named by
`/heimdall approve <draft_id>`) and approves the hash in that post's marker with `via="message"`.
A replaced draft gets a short "approve the latest draft" reply.

Non-maintainers receive one short thread reply. They create no approval or GitHub outbox entry.

`@bifrost ping` is a plain health check (`_health.py`): the bridge answers it with a fixed line in
the ping's thread, before any approval handling. Bifröst is code, not an agent, so this is the one
keyword match left. Messages to Huginn, Loki and Heimdall never reach the bridge; each agent hears
its own mentions and queues an `agent.mention` job (see `agents/README.md`).

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
