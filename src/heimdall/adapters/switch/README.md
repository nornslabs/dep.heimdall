# Switch adapter

`RoomPort` is the bridge boundary for opening PR threads, posting thread replies or
role mentions, reacting, and receiving room posts/reactions. `SwitchSender` implements
it through Mattermost-compatible REST and WebSocket APIs. It also delivers `switch`
outbox messages; those contain `channel_id` and `message`, with optional `root_id` for
a thread reply. The worker enables outbox delivery only when `HEIMDALL_SWITCH_URL`
and `HEIMDALL_SWITCH_TOKEN` are configured.

`run_inbound_events` forwards the event stream to an injected handler. Handler failures
are logged with event and post identifiers only; message bodies stay out of logs.

The adapter authenticates REST and WebSocket requests with the bot bearer token,
reconnects its event stream with capped exponential backoff, and maps network failures,
timeouts, rate limits and server errors to retryable `SwitchUnavailable`. Client errors
are permanent. The outbox key is attached to post props for tracing. Mattermost does
not enforce uniqueness for that property, so a crash after the server accepts a post
but before the outbox transaction commits can produce a duplicate on retry.
