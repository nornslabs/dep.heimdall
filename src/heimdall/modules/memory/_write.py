"""Remember and forget agent notes; only for a maintainer of the note's repo."""

from sqlalchemy import delete, func, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.memory._read import count_notes, read_note
from heimdall.modules.memory.domain import (
    MAX_NOTES_PER_AGENT,
    AgentNote,
    NoteInput,
    NoteScope,
    validate_key,
    validate_note,
)
from heimdall.modules.memory.models import AgentMemory
from heimdall.modules.tenancy.api import repo_is_enabled, repo_maintainers
from heimdall.shared.context import TenantCtx
from heimdall.shared.errors import (
    InvalidNote,
    NotAMaintainer,
    NoteLimitReached,
    NoteNotFound,
    RepoNotFound,
)

_LOCK_NOTES = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")


async def remember_note(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    scope: NoteScope,
    key: str,
    value: str,
    requested_by: str,
    source_message_id: str,
) -> AgentNote:
    """Store a note a maintainer asked an agent to keep, in its own transaction.

    See ``remember_note_in_transaction`` for the arguments and errors.
    """
    async with unit_of_work(sessions) as session:
        return await remember_note_in_transaction(
            session, ctx, scope, key, value, requested_by, source_message_id
        )


async def remember_note_in_transaction(
    session: AsyncSession,
    ctx: TenantCtx,
    scope: NoteScope,
    key: str,
    value: str,
    requested_by: str,
    source_message_id: str,
) -> AgentNote:
    """Store a note a maintainer asked an agent to keep, replacing any note with the same key.

    Every check runs before anything is written, so a refused note leaves the transaction clean.

    Args:
        session: Session inside the caller's transaction.
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
    await _require_maintainer(session, ctx, scope, requested_by)
    await _require_free_slot(session, ctx, scope, note.key)
    await _upsert(session, ctx, scope, note, requested_by, source_message_id)
    stored = await read_note(session, ctx, scope, note.key)
    if stored is None:
        raise RepoNotFound("the note's key is held by another installation")
    return stored


async def forget_note(
    sessions: async_sessionmaker[AsyncSession],
    ctx: TenantCtx,
    scope: NoteScope,
    key: str,
    requested_by: str,
) -> None:
    """Delete one note a maintainer asked an agent to drop, in its own transaction.

    See ``forget_note_in_transaction`` for the arguments and errors.
    """
    async with unit_of_work(sessions) as session:
        await forget_note_in_transaction(session, ctx, scope, key, requested_by)


async def forget_note_in_transaction(
    session: AsyncSession,
    ctx: TenantCtx,
    scope: NoteScope,
    key: str,
    requested_by: str,
) -> str:
    """Delete one note a maintainer asked an agent to drop.

    Args:
        session: Session inside the caller's transaction.
        ctx: Tenant and request context.
        scope: The repository and agent the note belongs to.
        key: Key of the note to delete.
        requested_by: GitHub login of the person who asked; must be a repo maintainer.

    Returns:
        The normalised key that was deleted.

    Raises:
        InvalidNote: The key is malformed.
        RepoNotFound: The repository is not enabled for this installation.
        NotAMaintainer: ``requested_by`` is not on the repo's maintainer list.
        NoteNotFound: No note with this key exists for the repository and agent.
    """
    normalised_key = validate_key(key)
    await _require_maintainer(session, ctx, scope, requested_by)
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
    if (await session.exec(statement)).first() is None:
        raise NoteNotFound(f"no note named {normalised_key!r}")
    return normalised_key


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


async def _require_free_slot(
    session: AsyncSession, ctx: TenantCtx, scope: NoteScope, key: str
) -> None:
    """Refuse a new note once the agent already keeps the most it may for this repository.

    Replacing a note that exists never needs a slot. The scope is locked until the transaction
    ends, so two notes written together cannot both take the last slot.

    Raises:
        NoteLimitReached: The agent keeps ``MAX_NOTES_PER_AGENT`` notes and ``key`` is new.
    """
    connection = await session.connection()
    lock_key = f"notes:{ctx.installation_id}:{scope.repo_id}:{scope.agent.value}"
    await connection.execute(_LOCK_NOTES, {"key": lock_key})
    if await read_note(session, ctx, scope, key) is not None:
        return
    if await count_notes(session, ctx, scope) >= MAX_NOTES_PER_AGENT:
        raise NoteLimitReached(
            f"an agent keeps at most {MAX_NOTES_PER_AGENT} notes per repo; forget one first"
        )


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
