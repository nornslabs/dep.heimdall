"""Writes outbox rows inside the caller's transaction (AGENTS.md rule 6)."""

import uuid

from sqlalchemy.dialects.postgresql import insert
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.columns import JsonObject
from heimdall.adapters.db.outbox.models import OutboxMessage
from heimdall.shared.ids import InstallationId

PENDING = "pending"


async def write_outbox(
    session: AsyncSession,
    *,
    installation_id: InstallationId,
    channel: str,
    kind: str,
    payload: JsonObject,
    idempotency_key: str,
) -> None:
    """Queue a message for GitHub or Switch; it is sent only if the transaction commits.

    Writing the same ``idempotency_key`` again does nothing, so a retried job can repeat its
    writes without failing its whole use case.

    Args:
        session: A session inside ``unit_of_work``.
        installation_id: The tenant the message belongs to.
        channel: ``github`` or ``switch``.
        kind: What the message is, for example ``pr.comment``.
        payload: What the channel sender needs to deliver it.
        idempotency_key: Unique per message; for GitHub writes it is the draft id.
    """
    statement = (
        insert(OutboxMessage)
        .values(
            # Core inserts skip the model's Python defaults, so the id is set here.
            id=uuid.uuid4(),
            installation_id=installation_id,
            channel=channel,
            kind=kind,
            payload=payload,
            idempotency_key=idempotency_key,
            status=PENDING,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
    )
    await session.exec(statement)


async def outbox_message_exists(
    session: AsyncSession, *, installation_id: InstallationId, idempotency_key: str
) -> bool:
    """Say whether a message with this key was already queued, so a redelivered job can stop.

    Args:
        session: A session inside ``unit_of_work``.
        installation_id: The tenant the message belongs to.
        idempotency_key: The key the message was written with.

    Returns:
        True when the tenant already has a message with this key.
    """
    statement = select(OutboxMessage.id).where(
        col(OutboxMessage.installation_id) == installation_id,
        col(OutboxMessage.idempotency_key) == idempotency_key,
    )
    return (await session.exec(statement)).first() is not None
