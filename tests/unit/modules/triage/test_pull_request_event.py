"""pull_request webhook payloads map to state machine events."""

from collections.abc import Mapping

import pytest

from heimdall.modules.triage._webhook_event import pull_request_event
from heimdall.modules.triage.domain import EventKind


def _payload() -> Mapping[str, object]:
    return {
        "repository": {"id": 7, "full_name": "acme/widgets"},
        "pull_request": {
            "number": 24,
            "user": {"login": "octocat"},
            "head": {"sha": "a" * 40},
            "base": {"sha": "b" * 40, "ref": "main"},
        },
    }


@pytest.mark.parametrize(
    ("action", "kind"),
    [
        ("opened", EventKind.PR_OPENED),
        ("synchronize", EventKind.PR_SYNCHRONIZE),
        ("edited", EventKind.PR_EDITED),
        ("labeled", EventKind.PR_EDITED),
        ("unlabeled", EventKind.PR_EDITED),
        ("assigned", EventKind.PR_EDITED),
        ("unassigned", EventKind.PR_EDITED),
        ("closed", EventKind.PR_CLOSED),
    ],
)
def test_supported_actions_map_to_event_kinds(action: str, kind: EventKind) -> None:
    event = pull_request_event("delivery-1", "pull_request", action, _payload())

    assert event is not None
    assert event.kind is kind


def test_unsupported_action_is_ignored() -> None:
    assert pull_request_event("delivery-1", "pull_request", "review_requested", _payload()) is None
