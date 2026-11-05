"""Read agent notes: this tenant's unexpired notes for one repo and agent."""

from sqlalchemy import func, or_
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.memory.domain import AgentNote, NoteScope, matches_query
from heimdall.modules.memory.models import AgentMemory
from heimdall.shared.context import TenantCtx


async def recall_notes(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    scope: NoteScope,
    query: str = "",
) -> tuple[AgentNote, ...]:
    """List an agent's unexpired notes for a repository that match a query.

    Args:
        sessions: Tenant database session factory.
        ctx: Tenant and request context.
        scope: The repository and agent whose notes are read.
        query: Text to find in a key or value, ignoring case; empty returns every note.

    Returns:
        Matching notes ordered by key; empty when none exist for this tenant.
    """
    async with unit_of_work(sessions) as session:
        return await recall_notes_in_transaction(session, ctx, scope, query)


async def recall_notes_in_transaction(
    session: AsyncSession, ctx: TenantCtx, scope: NoteScope, query: str = ""
) -> tuple[AgentNote, ...]:
    """List an agent's unexpired notes inside the caller's transaction.

    Args:
        session: Session inside the caller's transaction.
        ctx: Tenant and request context.
        scope: The repository and agent whose notes are read.
        query: Text to find in a key or value, ignoring case; empty returns every note.

    Returns:
        Matching notes ordered by key; empty when none exist for this tenant.
    """
    statement = (
        scoped_notes(ctx, scope)
        .where(
            or_(
                col(AgentMemory.expires_at).is_(None),
                col(AgentMemory.expires_at) > func.now(),
            )
        )
        .order_by(col(AgentMemory.key))
    )
    notes = tuple(to_note(row) for row in await session.exec(statement))
    return tuple(note for note in notes if matches_query(note, query))


async def count_notes(session: AsyncSession, ctx: TenantCtx, scope: NoteScope) -> int:
    """Count every note an agent keeps for a repository, expired or not, since each holds a slot."""
    statement = (
        select(func.count())
        .select_from(AgentMemory)
        .where(
            col(AgentMemory.installation_id) == ctx.installation_id,
            col(AgentMemory.repo_id) == scope.repo_id,
            col(AgentMemory.agent) == scope.agent.value,
        )
    )
    return (await session.exec(statement)).one()


async def read_note(
    session: AsyncSession, ctx: TenantCtx, scope: NoteScope, key: str
) -> AgentNote | None:
    """Read one note by key inside the caller's transaction."""
    row = (await session.exec(scoped_notes(ctx, scope).where(AgentMemory.key == key))).first()
    return None if row is None else to_note(row)


def scoped_notes(ctx: TenantCtx, scope: NoteScope) -> SelectOfScalar[AgentMemory]:
    """Select this tenant's notes for one repo and agent."""
    return select_for_tenant(AgentMemory, ctx.installation_id).where(
        AgentMemory.repo_id == scope.repo_id,
        AgentMemory.agent == scope.agent.value,
    )


def to_note(row: AgentMemory) -> AgentNote:
    """Copy a table row into the value other modules see."""
    return AgentNote(
        key=row.key,
        value=row.value,
        created_by=row.created_by,
        source_message_id=row.source_message_id,
        created_at=row.created_at,
        expires_at=row.expires_at,
    )
