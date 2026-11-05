"""Tests for parsing the hidden approval metadata on Switch posts."""

import uuid

from apps.bridge.approval import parse_approval_marker
from heimdall.modules.desk.api import format_approval_message
from heimdall.shared.ids import InstallationId


def test_approval_marker_round_trips_from_rendered_draft() -> None:
    """Keep the message marker bound to the saved draft and tenant."""
    draft_id = uuid.uuid4()
    body_hash = "a" * 64
    message = format_approval_message(
        "Reviewed draft", draft_id, body_hash, 12345, "owner/repository"
    )

    marker = parse_approval_marker(message)

    assert marker is not None
    assert marker.draft_id == draft_id
    assert marker.draft_hash == body_hash
    assert marker.installation_id == InstallationId(12345)
    assert marker.repo_full_name == "owner/repository"


def test_approval_marker_rejects_malformed_hash() -> None:
    """Ignore posts whose marker cannot identify an exact saved draft."""
    message = (
        "<!-- heimdall-approval draft_id=00000000-0000-0000-0000-000000000001 "
        "draft_hash=short installation_id=12345 repo=owner/repository -->"
    )

    assert parse_approval_marker(message) is None


def test_rendered_marker_is_a_hidden_link_definition_not_visible_text() -> None:
    """Markdown renderers drop a link definition, so the room shows only the draft."""
    message = format_approval_message("Reviewed draft", uuid.uuid4(), "a" * 64, 1, "o/r")

    assert "<!--" not in message
    assert message.splitlines()[-1].startswith("[//]: # (heimdall-approval ")


def test_legacy_html_comment_marker_still_parses() -> None:
    """Posts made before the marker was hidden stay approvable."""
    draft_id = uuid.uuid4()
    legacy = (
        f"Old draft\n\n<!-- heimdall-approval draft_id={draft_id} draft_hash={'b' * 64} "
        "installation_id=7 repo=o/r -->"
    )

    marker = parse_approval_marker(legacy)

    assert marker is not None
    assert marker.draft_id == draft_id
