"""Public memory interface; other modules import only from this file."""

from heimdall.modules.memory.domain import (
    MAX_NOTE_KEY_CHARS,
    MAX_NOTE_VALUE_CHARS,
    AgentNote,
    NoteInput,
    NoteScope,
    matches_query,
    validate_key,
    validate_note,
)
from heimdall.modules.memory.service import forget_note, recall_notes, remember_note

__all__ = [
    "MAX_NOTE_KEY_CHARS",
    "MAX_NOTE_VALUE_CHARS",
    "AgentNote",
    "NoteInput",
    "NoteScope",
    "forget_note",
    "matches_query",
    "recall_notes",
    "remember_note",
    "validate_key",
    "validate_note",
]
