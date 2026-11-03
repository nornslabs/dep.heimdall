# adapters/queue

The Postgres job queue (procrastinate) on Heimdall's own database. Its tables are installed by
the `507f7ae60c4c` migration from procrastinate's bundled `schema.sql`.

| File | Public names |
|---|---|
| `enqueue.py` | `enqueue(session, task_name, args, *, queue, lock, queueing_lock, schedule_at) -> job id`: adds a job **inside the caller's transaction**, so it exists only if the state change commits (AGENTS.md rule 7) |
| `app.py` | `create_queue_app(database_url, schema=None)`: the procrastinate `App` the worker runs; modules register tasks on it |

```python
async with unit_of_work(sessions) as session:
    pr.state = "received"
    await enqueue(session, "intake.run_intake", {"installation_id": ctx.installation_id, "pr_id": str(pr.id)})
```

- **Why not `task.defer_async()`:** procrastinate has no asyncpg connector, so its own defer would
  use a separate connection outside our transaction. `enqueue()` calls procrastinate's
  `procrastinate_defer_jobs_v1` SQL function through our session instead.
- **The worker** runs jobs through procrastinate's psycopg connector, which needs
  `psycopg[binary]` for libpq.
- **Queues:** `DEFAULT_QUEUE` holds every job the main worker (`apps/worker`) runs.
  `HUGINN_QUEUE` holds only `huginn.run_repro`, which the `huginn` service runs. Each process
  consumes only its own queue, because a worker that claims a task it never registered fails the
  job and nobody retries it.
- **Job args** are JSON and always include `installation_id`. procrastinate's own tables are the
  one exception to the "every table has `installation_id`" rule, as the LLD notes.
- **Upgrading procrastinate** means a new alembic revision that runs its migration files.

Tables: procrastinate's own (`procrastinate_jobs`, `procrastinate_events`, ...); alembic's
autogenerate ignores them (`include_in_migrations` in `adapters/db/metadata.py`).
