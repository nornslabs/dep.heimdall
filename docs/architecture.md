# Architecture

**Audience:** contributors, operators, reviewers.
**Purpose:** how the code is partitioned, what each piece owns, and the architectural rules that
every change must keep.

## Layout

```
apps/            entry points only: api, worker, mcp, bridge, admin. No business logic.
src/heimdall/
  modules/       one folder per feature: tenancy, triage, intake, repro, review, desk,
                 approval, audit, memory, conversation
  adapters/      the outside world: github, actions, switch, db, ai, queue
  shared/        settings, ids, errors, events, logging
agents/          huginn, loki, heimdall: prompt.md, agent.py, allowed_tools.py
migrations/      alembic
tests/           unit/ (domain), integration/ (adapters, DB), e2e/
```

### Inside a module

```
modules/<name>/
  api.py          PUBLIC. The only file other modules may import.
  service.py      use cases. One database transaction per public function.
  domain.py       pure rules and types. No I/O.
  ports.py        Protocols for what the module needs from outside (GitHubReader, Clock...).
  models.py       this module's tables. Nobody else reads or writes them.
  _<topic>.py     PRIVATE helpers, split out to keep files short. Never imported outside the module.
```

## Module borders

Import another module only through its `api.py`. `from heimdall.modules.intake.api import
run_intake` is fine. Importing `heimdall.modules.intake.models.IntakeResult` from another module is
not. Private modules and functions (names starting with `_`) are private to their module and never
imported across a module border.

## Pure domain

`domain.py` must not import FastAPI, SQLModel, githubkit, httpx, or anything from `adapters/`. It
takes values and returns values.

## Adapters implement ports

Services depend on the Protocol in `ports.py`, never on a concrete adapter. Wiring happens in
`apps/*/main.py`.

## One transaction per use case

A service function either commits everything it changed or nothing. Side effects (GitHub writes,
Switch posts) are never called from a service; they are written as an `outbox` row in the same
transaction, and the outbox sender delivers them with an idempotency key.

## Jobs go in the Postgres queue

Jobs are enqueued inside the same transaction as the state change, using Procrastinate.

## The state machine owns PR state

Only `triage` changes `prs.state`, and only through `triage.domain.next_state(...)`.

## Tenancy everywhere

Every table has `installation_id`. Every query filters on it. Every service function takes it
(directly or inside a context object). No exceptions. There is an integration test
(`test_every_table_has_installation_id`) that enforces this.

## Adapters and what each owns

| Adapter | Owns |
|---|---|
| `adapters/github` | Read-only GitHub access as a GitHub App installation. `reader.py` (`MAX_DIFF_BYTES`), `auth.py` (app JWT + installation token), `actions.py` (bounded paired run artifacts). Never writes to GitHub. |
| `adapters/github/sender.py` | Gjallarhorn: the only GitHub writer. Rechecks the approved draft hash and posts exact text as a PR comment. |
| `adapters/switch` | Switch room posts, thread routing, bot reactions, presence handling. |
| `adapters/db` | Postgres engine, unit of work, tenant filter, outbox table/writer/sender. |
| `adapters/ai` | Model provider adapter (`AiAdapter`); agents call `ai.chat(...)`, never a vendor SDK. |
| `adapters/queue` | Procrastinate app, enqueue helpers. |

Value types live in `shared/github.py` (`RepoRef`, `PullRequestInfo`, `ChangedFile`, `PrDiff`,
`CiState`), so module ports can name them without importing the GitHub adapter.

## Apps and what each runs

| App | Role |
|---|---|
| `apps/api` | Accepts GitHub deliveries at `POST /webhooks/github`; verifies signature on the raw body, dedupes `X-GitHub-Delivery`, queues handling. `/healthz` checks Postgres and queued job count. |
| `apps/worker` | Runs queued jobs and the outbox sender loop. Tasks: `intake.run_intake`, `triage.handle_event`, `repro.timeout`, `actions.process_workflow_run`, `tenancy.on_installation_event`. Outbox channels: `switch` and `github`. |
| `apps/mcp` | Tenant-scoped MCP tools for the agents. One server per agent identity, binding only that agent's allowed tool set. |
| `apps/bridge` | Streams room events, records maintainer approvals from thumbs-up reactions, runs first-time setup and agent registration. |
| `apps/admin` | One-off `link_room` to re-link a repo to a room. |

Agent jobs (`huginn.run_repro`, `loki.run_review`, `heimdall.*`) run in dedicated agent services,
not in the worker.

## Agents and their submit contracts

| Agent | Submit tool | What it produces |
|---|---|---|
| Huginn | `huginn.run_repro` | `HuginnVerdict` (`reproduced` / `not_covered` / `setup_failed` / `inconclusive`) |
| Loki | `loki.run_review` | `LokiReview` (saved finding ids or a skip reason) |
| Heimdall | `heimdall.build_card`, `heimdall.run_command` | `DeskResult` (`card`, `draft`, `mechanical_task` or `explanation`) |

The runtime (`agents/_runtime.py`) never posts; it returns a `RunOutcome` and the caller turns that
into a room card through the outbox. The model chooses its steps; the runtime's ToolBox, budgets,
guardrails and `submit_*` tools decide what it may do.

## Architectural rules (from AGENTS.md)

1. Module borders — import via `api.py` only.
2. Private modules — `_` names never cross a module border.
3. Pure domain — `domain.py` has no I/O and no framework imports.
4. Adapters implement ports — services depend on `ports.py` Protocols.
5. One transaction per use case — commit all or nothing.
6. Outbox for side effects — never call GitHub or Switch from a service; write an `outbox` row.
7. Jobs in the Postgres queue — enqueued inside the same transaction as the state change.
8. State machine — only `triage.domain.next_state` changes `prs.state`.
9. Tenancy everywhere — every table has `installation_id`, every query filters on it.

## Security rules in architecture

- PR titles, bodies, diffs, comments, and test output are untrusted data: returned as values,
  never logged, never passed to a shell, never treated as instructions.
- Only Gjallarhorn writes to GitHub, and only when an approval exists whose `draft_hash` matches
  the text being posted.
- Mechanical tasks post under the `auto_suggest` policy and still go through an approval row with
  `approver = "policy:auto_suggest"`.
- No `pull_request_target`. The repro workflow gets `permissions: contents: read` and no secrets.
- Pass user-controlled values to workflows through `env:`, never inline in `run:`.
- Verify the webhook signature before reading the body; deduplicate on `X-GitHub-Delivery`.
- Never log tokens, keys, private-key paths or full PR diffs. Log ids.