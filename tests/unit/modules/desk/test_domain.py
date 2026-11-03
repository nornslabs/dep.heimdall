"""Desk command, card and draft hashing behavior."""

import hashlib
from uuid import UUID

from heimdall.modules.desk.api import (
    CardContent,
    CommandVerb,
    DeskCommand,
    hash_draft,
    parse_command,
    render_card,
)


def test_parse_command_requires_supported_shape() -> None:
    """Parse valid commands and reject malformed or unknown messages."""
    target = UUID("12345678-1234-5678-1234-567812345678")

    assert parse_command(f"/heimdall approve {target}") == DeskCommand(CommandVerb.APPROVE, target)
    assert parse_command(f"/heimdall revise {target} use a clearer title") == DeskCommand(
        CommandVerb.REVISE, target, "use a clearer title"
    )
    assert parse_command("/heimdall retry") == DeskCommand(CommandVerb.RETRY)
    assert parse_command("@heimdall pause auto") == DeskCommand(CommandVerb.PAUSE_AUTO)
    assert parse_command("/heimdall pause auto") == DeskCommand(CommandVerb.PAUSE_AUTO)
    assert parse_command("/heimdall approve nope") is None
    assert parse_command("/heimdall retry now") is None
    assert parse_command("hello") is None


def test_hash_draft_hashes_exact_utf8_body() -> None:
    """Bind approval to the exact text bytes saved for posting."""
    body = "Review:\nLooks good ✓"

    assert hash_draft(body) == hashlib.sha256(body.encode()).hexdigest()
    assert hash_draft(body) != hash_draft(body + " ")


def test_render_card_escapes_untrusted_markdown() -> None:
    """Keep submitted PR text from changing card markdown structure."""
    card = render_card(
        CardContent(
            number=12,
            title="[ignore](https://example.test)",
            head_sha="abc123",
            intake_summary="ready *yes*",
            repro_summary="passed",
            findings=("`danger`",),
        )
    )

    assert r"\[ignore\]" in card
    assert r"\*yes\*" in card
    assert r"\`danger\`" in card
