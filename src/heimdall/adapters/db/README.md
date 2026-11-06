# adapters/db

Postgres access for every module. Modules define their tables in their own `models.py`;
this adapter supplies the engine, the transaction boundary and the tenant filter.

| File | Public names |
|---|---|
| `engine.py` | `create_engine(database_url, schema=None)`, `make_session_factory(engine)`, `to_async_url(url)` |
| `unit_of_work.py` | `unit_of_work(sessions)`: one transaction per use case; commits on success, rolls back on error |
| `tenant.py` | `TenantScoped`: base class every table model extends (adds indexed `installation_id`); `select_for_tenant(model, installation_id)`: start every read here |
| `columns.py` | Field helpers so tables agree on types: `uuid_pk()`, `bigint_pk()` and `bigint()` (GitHub ids overflow INT), `set_on_insert()` and `timestamp()` (TIMESTAMPTZ), `jsonb()` (defaults to `'{}'`) |
| `outbox/models.py` | `OutboxMessage`, the `outbox` table |
| `outbox/writer.py` | `write_outbox(session, *, installation_id, channel, kind, payload, idempotency_key)`: inside the caller's transaction; the same key twice is a no-op |
| `outbox/sender.py` | `ChannelSender` protocol, `send_due_messages(sessions, senders, *, now)`, `run_outbox_loop(sessions, senders, stop)`: the worker's 1 s poll |
| `metadata.py` | `metadata`, `import_all_models()`, `include_in_migrations()`: used by alembic and the test schema fixture |

```python
class Draft(TenantScoped, table=True):
    id: int | None = Field(default=None, primary_key=True)
    body: str

async with unit_of_work(sessions) as session:
    drafts = await session.exec(select_for_tenant(Draft, ctx.installation_id))
```

Tables: `outbox`. Integration tests get a fresh schema per session
(`tests/integration/db/conftest.py`); `test_every_table_has_installation_id` enforces rule 9.

## Outbox delivery

Each due message is locked (`FOR UPDATE SKIP LOCKED`), sent and recorded in its own transaction,
so two workers never send the same message. If a worker crashes mid-send, the message is sent
again, so every `ChannelSender` must be idempotent per `idempotency_key`.

- **Success:** `status = sent`.
- **Retryable failure** (any exception except a non-retryable `HeimdallError`): retried after
  2 s, 8 s, 30 s, 2 m, 10 m, so at most 6 sends.
- **After the last retry**, or on a non-retryable error: `status = dead`, plus a `switch`
  message `alert.outbox_dead` in the dead message's own room (its `channel_id`), in words that
  name the outbox id, channel, kind and error type but never the message content. A dead row with
  no `channel_id` and no `notify` block is logged as `outbox_alert_skipped` and raises no alert.
  A GitHub row queued by an approval carries `notify` (agent room, PR, agent, draft kind); when
  such a row dies, the PR's thread is told in plain words, as that agent, that GitHub refused the
  action (for example a merge on a PR that changed), without GitHub's own reply.
  Alerts never raise alerts.
- **`last_error`** stores only the error type, since error text can quote PR content.
No sender for a channel: the message stays `pending`.

## Related documentation

- `docs/architecture.md` — outbox writer/sender, tenant filter, one transaction per use case.
- `docs/how-it-works.md` — outbox delivery between state changes and external posts.
