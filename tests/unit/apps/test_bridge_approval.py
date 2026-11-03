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
