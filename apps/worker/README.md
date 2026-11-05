# Worker process

Background worker that runs queued jobs and the outbox sender loop side by side until stopped.
Start with `python -m apps.worker.main` after setting the required `HEIMDALL_*` process settings.

It wires settings, database, the Procrastinate queue app and the GitHub/Switch senders, then
registers one task per module job:

|| Task | Owner |
|---|---|---|
| `intake.run_intake` | `intake` — applies `heimdall.yml` rules for a PR head |
| `triage.handle_event` | `triage` — applies one persisted GitHub delivery to the state machine |
| `repro.timeout` | `repro` — finishes reproduction when its paired run does not return |
| `actions.process_workflow_run` | `repro` — ingests paired workflow artifact outcomes |
| `tenancy.on_installation_event` | `tenancy` — applies installation lifecycle deliveries |

Huginn's `huginn.run_repro`, Loki's `loki.run_review` and Heimdall's `heimdall.*` jobs run in the
dedicated agent services, not here.

Outbox channels: `switch` (room messages, cards, drafts) and `github` (approved comments and
reproduction workflow dispatches). A `SwitchRoutingSender` routes switch posts to either the bot
or an agent poster keyed by the thread store; a `GitHubSender` posts GitHub comments through the
approval outbox. Both run in the same transaction as the state change they follow.

|| File | Role |
|---|---|---|
|| `main.py` | `run_worker`: settings, engine, queue app, senders, task registration, run loop |
|| `tasks.py` | `register_*_task`: one Procrastinate task per module job |
|| `_threads.py` | `SessionThreadStore`: maps PR threads to outbox sessions |
