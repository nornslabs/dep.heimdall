# API process

The API accepts GitHub deliveries at `POST /webhooks/github`. It authenticates the exact raw
body before parsing, then records each `X-GitHub-Delivery` once and queues handling in the same
transaction. Installation lifecycle deliveries use the tenancy handler; pull request deliveries
use triage. `/healthz` checks Postgres and reports the count of queued jobs.

Run with `python -m apps.api.main` after setting the required `HEIMDALL_*` process settings.

## Related documentation

- `docs/architecture.md` — the API verifies webhook signatures and queues handling in-process.
- `docs/setup.md` — the API listens at host port 8001.
