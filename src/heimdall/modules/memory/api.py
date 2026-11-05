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
from heimdall.modules.memory.service import (
    forget_note,
    forget_note_in_transaction,
    recall_notes,
    remember_note,
    remember_note_in_transaction,
)

__all__ = [
    "MAX_NOTE_KEY_CHARS",
    "MAX_NOTE_VALUE_CHARS",
    "AgentNote",
    "NoteInput",
    "NoteScope",
    "forget_note",
    "forget_note_in_transaction",
    "matches_query",
    "recall_notes",
    "remember_note",
    "remember_note_in_transaction",
    "validate_key",
    "validate_note",
]
