"""Enqueue procrastinate jobs inside the caller's transaction (AGENTS.md rule 7).

procrastinate's own ``defer`` uses a separate connection, so a job could exist for a state
change that rolled back. Calling its defer function through our session avoids that.
"""

import json
from datetime import datetime

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.columns import JsonObject

DEFAULT_QUEUE = "default"
# Huginn's process registers only its own task, so its jobs live on their own queue and
# the main worker never claims them (nor Huginn the main worker's).
HUGINN_QUEUE = "huginn"
# Loki and Heimdall are separate processes too, each claiming only its own task.
LOKI_QUEUE = "loki"
HEIMDALL_QUEUE = "heimdall"
DEFAULT_PRIORITY = 0

DEFER_JOB = text(
    """
    SELECT unnest(procrastinate_defer_jobs_v1(ARRAY[ROW(
        CAST(:queue AS varchar),
        CAST(:task_name AS varchar),
        CAST(:priority AS integer),
        CAST(:lock AS text),
        CAST(:queueing_lock AS text),
        CAST(:args AS jsonb),
        CAST(:scheduled_at AS timestamptz)
    )::procrastinate_job_to_defer_v1])) AS id
    """
)


async def enqueue(
    session: AsyncSession,
    task_name: str,
    args: JsonObject,
    *,
    queue: str = DEFAULT_QUEUE,
    lock: str | None = None,
    queueing_lock: str | None = None,
    schedule_at: datetime | None = None,
) -> int:
    """Add a job that becomes visible to workers only if the transaction commits.

    Args:
        session: A session inside ``unit_of_work``.
        task_name: The registered procrastinate task name, for example ``triage.handle_event``.
        args: JSON keyword arguments for the task; always include ``installation_id``.
        queue: Queue the job goes to.
        lock: Jobs sharing a lock run one at a time.
        queueing_lock: At most one waiting job per value; a second enqueue fails.
        schedule_at: Run no earlier than this time; ``None`` means now.

    Returns:
        The new job's id.
    """
    # Raw SQL goes through the session's connection, so it joins the open transaction.
    connection = await session.connection()
    result = await connection.execute(
        DEFER_JOB,
        {
            "queue": queue,
            "task_name": task_name,
            "priority": DEFAULT_PRIORITY,
            "lock": lock,
            "queueing_lock": queueing_lock,
            "args": json.dumps(args),
            "scheduled_at": schedule_at,
        },
    )
    return result.scalar_one()
