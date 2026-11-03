"""Public interface for desk functionality."""

from heimdall.modules.desk.domain import (
    CardContent,
    CardRef,
    CommandVerb,
    DeskCommand,
    DraftRef,
    format_approval_message,
    hash_draft,
    parse_command,
    render_card,
)
from heimdall.modules.desk.service import (
    get_draft,
    get_draft_in_transaction,
    save_card,
    save_draft,
)

__all__ = [
    "CardContent",
    "CardRef",
    "CommandVerb",
    "DeskCommand",
    "DraftRef",
    "format_approval_message",
    "get_draft",
    "get_draft_in_transaction",
    "hash_draft",
    "parse_command",
    "render_card",
    "save_card",
    "save_draft",
]
