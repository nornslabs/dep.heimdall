"""Persist verified webhook deliveries and queue their handling atomically."""

import json
from typing import cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.columns import JsonObject
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.adapters.queue.enqueue import enqueue
from heimdall.shared.ids import InstallationId

INSERT_DELIVERY = text(
    """
    INSERT INTO events (delivery_id, installation_id, kind, action, payload)
    VALUES (:delivery_id, :installation_id, :kind, :action, CAST(:payload AS jsonb))
    ON CONFLICT (delivery_id) DO NOTHING
    RETURNING delivery_id
    """
)


async def receive_webhook(
    sessions: async_sessionmaker[AsyncSession],
    delivery_id: str,
    event_name: str,
    action: str | None,
    payload: JsonObject,
) -> bool:
    """Store a GitHub delivery once and enqueue its tenant-scoped handler.

    Args:
        sessions: Async session factory used by the API process.
        delivery_id: GitHub's unique delivery identifier.
        event_name: Value from ``X-GitHub-Event``.
        action: Optional action in the event payload.
        payload: Parsed, verified GitHub event object.

    Returns:
        True for a new delivery and False for a duplicate.
    """
    payload_values = cast(dict[str, object], payload)
    installation = payload_values.get("installation")
    if not isinstance(installation, dict):
        raise ValueError("webhook payload is missing installation.id")
    installation_values = cast(dict[str, object], installation)
    raw_installation_id = installation_values.get("id")
    if not isinstance(raw_installation_id, int):
        raise ValueError("webhook payload is missing installation.id")
    installation_id = InstallationId(raw_installation_id)
    async with unit_of_work(sessions) as session:
        connection = await session.connection()
        result = await connection.execute(
            INSERT_DELIVERY,
            {
                "delivery_id": delivery_id,
                "installation_id": installation_id,
                "kind": event_name,
                "action": action,
                "payload": json.dumps(payload),
            },
        )
        inserted = result.scalar_one_or_none()
        if inserted is None:
            return False
        task_name = (
            "tenancy.on_installation_event"
            if event_name in {"installation", "installation_repositories"}
            else "triage.handle_event"
        )
        await enqueue(
            session,
            task_name,
            {"installation_id": installation_id, "delivery_id": delivery_id},
        )
    return True
