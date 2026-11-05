"""Pure rules for agent notes: key format, value length and recall matching."""

import re
from dataclasses import dataclass
from datetime import datetime

from heimdall.shared.agents import AgentName
from heimdall.shared.errors import InvalidNote
from heimdall.shared.ids import RepoId

MAX_NOTE_VALUE_CHARS = 300
"""Longest note value; keeps recalled notes small inside the agent's prompt budget."""

MAX_NOTE_KEY_CHARS = 64
"""Longest note key."""

MAX_NOTES_PER_AGENT = 50
"""Most notes one agent keeps for one repository, so recalled notes stay a small, curated list."""

_KEY_PATTERN = re.compile(rf"[a-z0-9][a-z0-9_.-]{{0,{MAX_NOTE_KEY_CHARS - 1}}}")


@dataclass(frozen=True)
class NoteScope:
    """Whose notes these are: one agent's notes for one repository.

    Attributes:
        repo_id: GitHub repository the notes describe.
        agent: The agent that keeps and recalls them.
    """

    repo_id: RepoId
    agent: AgentName


@dataclass(frozen=True)
class NoteInput:
    """A validated note ready to store.

    Attributes:
        key: Lowercase key, unique per repo and agent.
        value: Trimmed note text, at most ``MAX_NOTE_VALUE_CHARS`` characters.
    """

    key: str
    value: str


@dataclass(frozen=True)
class AgentNote:
    """A stored note as other modules and agents see it.

    Attributes:
        key: The note's key.
        value: The note's text; untrusted when shown to a model.
        created_by: GitHub login of the maintainer who asked for it.
        source_message_id: Switch message that carried the request.
        created_at: When the note was first stored or last replaced.
        expires_at: When the note stops being recalled, or ``None`` for never.
    """

    key: str
    value: str
    created_by: str
    source_message_id: str
    created_at: datetime
    expires_at: datetime | None


def validate_key(key: str) -> str:
    """Normalise a note key and check its format, so keys stay short and plain.

    Args:
        key: Key as given by the maintainer.

    Returns:
        The trimmed, lowercase key.

    Raises:
        InvalidNote: The key is empty, too long or has characters outside ``[a-z0-9_.-]``.
    """
    normalised = key.strip().lower()
    if not _KEY_PATTERN.fullmatch(normalised):
        raise InvalidNote(
            f"note key must be 1-{MAX_NOTE_KEY_CHARS} characters of a-z, 0-9, '_', '.', '-' "
            "and start with a letter or digit"
        )
    return normalised


def validate_note(key: str, value: str) -> NoteInput:
    """Check a note before it is stored, so no oversized or empty note reaches the table.

    Args:
        key: Key as given by the maintainer.
        value: Note text as given by the maintainer.

    Returns:
        The note with a normalised key and trimmed value.

    Raises:
        InvalidNote: The key is malformed, or the value is empty or too long.
    """
    normalised_key = validate_key(key)
    trimmed = value.strip()
    if not trimmed:
        raise InvalidNote("note value must not be empty")
    if len(trimmed) > MAX_NOTE_VALUE_CHARS:
        raise InvalidNote(f"note value must be at most {MAX_NOTE_VALUE_CHARS} characters")
    return NoteInput(key=normalised_key, value=trimmed)


def matches_query(note: AgentNote, query: str) -> bool:
    """Tell whether a note answers a recall query, ignoring case.

    Args:
        note: The stored note.
        query: Text to look for; an empty query matches every note.

    Returns:
        True when the query appears in the note's key or value.
    """
    needle = query.strip().lower()
    if not needle:
        return True
    return needle in note.key.lower() or needle in note.value.lower()
