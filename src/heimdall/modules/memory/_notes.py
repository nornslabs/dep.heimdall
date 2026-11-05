"""Remember, recall and forget agent notes; writes only for a repo maintainer."""

from sqlalchemy import delete, func, or_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.memory.domain import (
    AgentNote,
    NoteInput,
    NoteScope,
    matches_query,
    validate_key,
    validate_note,
)
from heimdall.modules.memory.models import AgentMemory
from heimdall.modules.tenancy.api import repo_is_enabled, repo_maintainers
from heimdall.shared.context import TenantCtx
from heimdall.shared.errors import InvalidNote, NotAMaintainer, NoteNotFound, RepoNotFound


async def remember_note(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    scope: NoteScope,
    key: str,
    value: str,
    requested_by: str,
    source_message_id: str,
) -> AgentNote:
    """Store a note a maintainer asked an agent to keep, replacing any note with the same key.

    Args:
        sessions: Tenant database session factory.
        ctx: Tenant and request context.
        scope: The repository and agent the note belongs to.
        key: Note key as the maintainer gave it.
        value: Note text as the maintainer gave it.
        requested_by: GitHub login of the person who asked; must be a repo maintainer.
        source_message_id: Switch message that carried the request.

    Returns:
        The stored note.

    Raises:
        InvalidNote: The key or value breaks the memory rules, or the message id is empty.
        RepoNotFound: The repository is not enabled for this installation.
        NotAMaintainer: ``requested_by`` is not on the repo's maintainer list.
    """
    note = validate_note(key, value)
    if not source_message_id.strip():
        raise InvalidNote("a note needs the id of the message that asked for it")
    async with unit_of_work(sessions) as session:
        await _require_maintainer(session, ctx, scope, requested_by)
        await _upsert(session, ctx, scope, note, requested_by, source_message_id)
        stored = await _read_note(session, ctx, scope, note.key)
    if stored is None:
        raise RepoNotFound("the note's key is held by another installation")
    return stored


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
    statement = (
        _scoped(ctx, scope)
        .where(
            or_(
                col(AgentMemory.expires_at).is_(None),
                col(AgentMemory.expires_at) > func.now(),
            )
        )
        .order_by(col(AgentMemory.key))
    )
    async with unit_of_work(sessions) as session:
        notes = tuple(_to_note(row) for row in await session.exec(statement))
    return tuple(note for note in notes if matches_query(note, query))


async def forget_note(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    scope: NoteScope,
    key: str,
    requested_by: str,
) -> None:
    """Delete one note a maintainer asked an agent to drop.

    Args:
        sessions: Tenant database session factory.
        ctx: Tenant and request context.
        scope: The repository and agent the note belongs to.
        key: Key of the note to delete.
        requested_by: GitHub login of the person who asked; must be a repo maintainer.

    Raises:
        InvalidNote: The key is malformed.
        RepoNotFound: The repository is not enabled for this installation.
        NotAMaintainer: ``requested_by`` is not on the repo's maintainer list.
        NoteNotFound: No note with this key exists for the repository and agent.
    """
    normalised_key = validate_key(key)
    statement = (
        delete(AgentMemory)
        .where(
            col(AgentMemory.installation_id) == ctx.installation_id,
            col(AgentMemory.repo_id) == scope.repo_id,
            col(AgentMemory.agent) == scope.agent.value,
            col(AgentMemory.key) == normalised_key,
        )
        .returning(col(AgentMemory.id))
    )
    async with unit_of_work(sessions) as session:
        await _require_maintainer(session, ctx, scope, requested_by)
        deleted = (await session.exec(statement)).first()
        if deleted is None:
            raise NoteNotFound(f"no note named {normalised_key!r}")


async def _require_maintainer(
    session: AsyncSession, ctx: TenantCtx, scope: NoteScope, requested_by: str
) -> None:
    """Refuse a write unless the repo is this tenant's and the requester maintains it.

    This check is the memory guardrail: it runs on every write, whatever the caller, so a
    model steered by PR text cannot store or drop a note on its own.
    """
    if not await repo_is_enabled(session, ctx, scope.repo_id):
        raise RepoNotFound("repository is not enabled for this installation")
    configured = await repo_maintainers(session, ctx, scope.repo_id)
    maintainers = {login.casefold() for login in configured}
    if requested_by.strip().casefold() not in maintainers:
        raise NotAMaintainer("only a repo maintainer can change an agent's notes")


async def _upsert(
    session: AsyncSession,
    ctx: TenantCtx,
    scope: NoteScope,
    note: NoteInput,
    requested_by: str,
    source_message_id: str,
) -> None:
    """Insert the note or replace the one with the same key, never touching another tenant's."""
    changes = {
        "value": note.value,
        "created_by": requested_by.strip(),
        "source_message_id": source_message_id,
    }
    statement = insert(AgentMemory).values(
        installation_id=ctx.installation_id,
        repo_id=scope.repo_id,
        agent=scope.agent.value,
        key=note.key,
        **changes,
    )
    statement = statement.on_conflict_do_update(
        index_elements=["repo_id", "agent", "key"],
        set_={**changes, "created_at": func.now(), "expires_at": None},
        where=col(AgentMemory.installation_id) == ctx.installation_id,
    )
    await session.exec(statement)


async def _read_note(
    session: AsyncSession, ctx: TenantCtx, scope: NoteScope, key: str
) -> AgentNote | None:
    """Read one note by key inside the caller's transaction."""
    row = (await session.exec(_scoped(ctx, scope).where(AgentMemory.key == key))).first()
    return None if row is None else _to_note(row)


def _scoped(ctx: TenantCtx, scope: NoteScope) -> SelectOfScalar[AgentMemory]:
    """Select this tenant's notes for one repo and agent."""
    return select_for_tenant(AgentMemory, ctx.installation_id).where(
        AgentMemory.repo_id == scope.repo_id,
        AgentMemory.agent == scope.agent.value,
    )


def _to_note(row: AgentMemory) -> AgentNote:
    """Copy a table row into the value other modules see."""
    return AgentNote(
        key=row.key,
        value=row.value,
        created_by=row.created_by,
        source_message_id=row.source_message_id,
        created_at=row.created_at,
        expires_at=row.expires_at,
    )
