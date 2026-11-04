# Switch adapter

`RoomPort` is the bridge boundary for opening PR threads, posting thread replies or
role mentions, reacting, fetching reacted-to posts, resolving reactor usernames, and
receiving room posts/reactions. `SwitchSender` implements
it through Mattermost-compatible REST and WebSocket APIs. It also delivers `switch`
outbox messages; those contain `channel_id` and `message`, with optional `root_id` for
a thread reply. The worker enables outbox delivery only when `HEIMDALL_SWITCH_URL`
and `HEIMDALL_SWITCH_TOKEN` are configured; it wraps this sender in `SwitchRoutingSender`.

`run_inbound_events` forwards the event stream to an injected handler. Handler failures
are logged with event and post identifiers only; message bodies stay out of logs.

The adapter authenticates REST and WebSocket requests with the bot bearer token,
reconnects its event stream with capped exponential backoff, and maps network failures,
timeouts, rate limits and server errors to retryable `SwitchUnavailable`. Client errors
are permanent. The outbox key is attached to post props for tracing. Mattermost does
not enforce uniqueness for that property, so a crash after the server accepts a post
but before the outbox transaction commits can produce a duplicate on retry.

## Switch agent protocol (`agent/`)

`agent/` speaks the real Switch Rooms agent protocol for **one agent in one process**
(HEI-81). It is separate from the Mattermost `SwitchSender` above, which stays for
reactions and approvals because agent events carry no reactions.

`SwitchAgentClient` (see `AgentRoomPort`) opens
`/agents/{id}/connection/ws`, answers each `ping` with a `pong` carrying the processed
cursor, delivers addressed `message` events to a handler, and posts with
`POST /agents/{id}/message`. It is wired only in `apps/*/main.py`; the worker's use of posting
is described below, and inbound mentions are still to come.

Concurrency rules:

* One reader task owns the socket and answers pings before queueing anything. It never
  waits on a handler, so slow work cannot cause a `heartbeat_lapsed` eviction.
* One consumer task runs handlers one at a time, in order. `Progress` fields each have a
  single writer (reader: received/generation; consumer: processed).
* If the bounded queue is full the reader ends the session and reconnects from the last
  queued sequence instead of blocking; nothing is dropped.
* Delivery is at-least-once; replays are dropped by `message_id` (last 1000 ids). A
  handler error is logged by ids only and the cursor still advances.
* `run()` rejects a second concurrent call. Run one process per agent id: a second process
  would take the connection over and this one stops on `taken_over`.
* Posting is serialized per agent and **never retried here**: `/message` has no idempotency
  key. Failures raise `SwitchUnavailable` (retryable) or `SwitchRequestFailed`; the outbox
  owns retries.

Stops without reconnecting: `evicted` with `taken_over` or `closed`, and shutdown. Raises
`SwitchRequestFailed`: close 4401 (bad key), any 4xxx refusal, or a 4xx handshake answer.
Reconnects with `start_from` and `expected_generation`: every other eviction, close 1012 and
network errors, with capped exponential backoff. Message bodies are never logged. The cursor
is held in memory only, so a process restart resumes from `head`.

Switch's `/message` call takes only `room_id` and `content`, so an agent cannot reply into
a thread; plan on one room per PR.

## Posting as an agent from the outbox (`routing.py`, `agent/posters.py`)

`SwitchRoutingSender` is the worker's `switch` outbox sender. A row whose payload has an
`agent` (`huginn`, `loki` or `heimdall`, see `shared.agents.AgentName`) is posted through that
agent's `AgentPoster`, so it shows under the agent's own name. A row with no `agent` goes through
the Mattermost bot as before (triage checklist, dead-row alerts). A row that names an agent never
falls back to the bot: an unknown name, or an agent whose key is not configured, fails as
non-retryable `SwitchRequestFailed`, so the row goes dead and the room is told.

`build_agent_posters` reads `SWITCH_<AGENT>_AGENT_ID` and `_API_KEY` for each agent. An agent with
neither is left out; an agent with only one fails the worker's start naming the missing variable.
The worker needs no open WebSocket to post: `POST /agents/{id}/message` works on the key alone
(checked against Switch 0.31.0). The agent-post text reaches Mattermost unchanged, including the
HTML-comment approval marker, so 👍 approval through the bridge is unaffected.

The idempotency key stays on the outbox row. `/message` has no key of its own, so a crash after
Switch accepts a post can double-post on retry, as the Mattermost path can.

## Provisioning (`provisioning/`)

`AgentRegistrar` calls `POST /agents` with the bootstrap registration token and returns an
`AgentRegistration` (id and key; the key is hidden from `repr`). A 409 raises `SwitchAgentExists`.
`SwitchAdmin` signs in once as the Gateway admin and calls `PUT /agents/{id}/addressing-policy`;
that route needs a signed-in owner or admin, so the registration token is not enough.
`triage_room_policy` is the narrowest policy that works: one rule admitting people in the triage
room, no other agent and not the platform. Error messages carry the action and status only, never
a response body, token or password. `agent/credentials.py` reads an agent's `SWITCH_<AGENT>_*`
variables into an `AgentConnectionConfig`.
