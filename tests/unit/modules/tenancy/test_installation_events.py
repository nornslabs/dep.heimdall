"""parse_installation_event turns GitHub installation webhooks into tenancy changes."""

import pytest

from heimdall.modules.tenancy.domain import (
    InstallationAction,
    RepoSummary,
    parse_installation_event,
)
from heimdall.shared.errors import WebhookPayloadInvalid
from heimdall.shared.ids import InstallationId, RepoId

ACCOUNT = {"login": "octo-org", "type": "Organization", "id": 1}
REPO_A = {"id": 101, "full_name": "octo-org/alpha", "name": "alpha", "private": False}
REPO_B = {"id": 102, "full_name": "octo-org/beta", "name": "beta", "private": True}


def installation_payload(action: str, **extra: object) -> dict[str, object]:
    return {"action": action, "installation": {"id": 7, "account": ACCOUNT}, **extra}


def test_created_upserts_the_installation_with_all_its_repos() -> None:
    event = parse_installation_event(
        "installation", installation_payload("created", repositories=[REPO_A, REPO_B])
    )

    assert event is not None
    assert event.action is InstallationAction.UPSERT
    assert event.installation_id == InstallationId(7)
    assert event.account_login == "octo-org"
    assert event.account_type == "Organization"
    assert event.repos_enabled == (
        RepoSummary(repo_id=RepoId(101), full_name="octo-org/alpha"),
        RepoSummary(repo_id=RepoId(102), full_name="octo-org/beta"),
    )
    assert event.repos_disabled == ()


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("deleted", InstallationAction.SUSPEND),
        ("suspend", InstallationAction.SUSPEND),
        ("unsuspend", InstallationAction.UNSUSPEND),
        ("new_permissions_accepted", InstallationAction.UPSERT),
    ],
)
def test_installation_actions_map_to_tenancy_actions(
    action: str, expected: InstallationAction
) -> None:
    event = parse_installation_event("installation", installation_payload(action))

    assert event is not None
    assert event.action is expected
    assert event.repos_enabled == ()


def test_repositories_added_are_enabled() -> None:
    payload = installation_payload("added", repositories_added=[REPO_B], repositories_removed=[])

    event = parse_installation_event("installation_repositories", payload)

    assert event is not None
    assert event.action is InstallationAction.UPSERT
    assert event.repos_enabled == (RepoSummary(repo_id=RepoId(102), full_name="octo-org/beta"),)


def test_repositories_removed_are_disabled() -> None:
    payload = installation_payload("removed", repositories_added=[], repositories_removed=[REPO_A])

    event = parse_installation_event("installation_repositories", payload)

    assert event is not None
    assert event.repos_disabled == (RepoId(101),)
    assert event.repos_enabled == ()


@pytest.mark.parametrize(
    ("kind", "action"),
    [("installation", "deletedd"), ("pull_request", "opened"), ("installation_target", "renamed")],
)
def test_other_events_and_actions_are_ignored(kind: str, action: str) -> None:
    assert parse_installation_event(kind, installation_payload(action)) is None


def test_payload_without_installation_id_is_rejected() -> None:
    payload = {"action": "created", "installation": {"account": ACCOUNT}}

    with pytest.raises(WebhookPayloadInvalid, match=r"installation\.id"):
        parse_installation_event("installation", payload)
