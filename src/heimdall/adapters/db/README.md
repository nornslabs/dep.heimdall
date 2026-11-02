# adapters/db

Postgres access for every module. Modules define their tables in their own `models.py`;
this adapter supplies the engine, the transaction boundary and the tenant filter.

| File | Public names |
|---|---|
| `engine.py` | `create_engine(database_url, schema=None)`, `make_session_factory(engine)`, `to_async_url(url)` |
| `unit_of_work.py` | `unit_of_work(sessions)`: one transaction per use case; commits on success, rolls back on error |
| `tenant.py` | `TenantScoped`: base class every table model extends (adds indexed `installation_id`); `select_for_tenant(model, installation_id)`: start every read here |
| `columns.py` | Field helpers so tables agree on types: `uuid_pk()`, `bigint_pk()` and `bigint()` (GitHub ids overflow INT), `set_on_insert()` and `timestamp()` (TIMESTAMPTZ), `jsonb()` (defaults to `'{}'`) |
| `outbox.py` | `OutboxMessage`, the `outbox` table: every module writes it, the worker sends it (HEI-48) |
| `metadata.py` | `metadata`, `import_all_models()`: used by alembic and the test schema fixture |

```python
class Draft(TenantScoped, table=True):
    id: int | None = Field(default=None, primary_key=True)
    body: str

async with unit_of_work(sessions) as session:
    drafts = await session.exec(select_for_tenant(Draft, ctx.installation_id))
```

Tables: `outbox`. Integration tests get a fresh schema per session
(`tests/integration/db/conftest.py`); `test_every_table_has_installation_id` enforces rule 9.
