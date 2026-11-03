"""What GitHub installation webhooks mean for tenants and repos (pure)."""

from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, ValidationError

from heimdall.shared.errors import WebhookPayloadInvalid
from heimdall.shared.ids import InstallationId, RepoId

INSTALLATION = "installation"
INSTALLATION_REPOSITORIES = "installation_repositories"


class InstallationAction(StrEnum):
    """What to do to the installation row."""

    UPSERT = "upsert"
    SUSPEND = "suspend"
    UNSUSPEND = "unsuspend"


INSTALLATION_ACTIONS = {
    "created": InstallationAction.UPSERT,
    "new_permissions_accepted": InstallationAction.UPSERT,
    "deleted": InstallationAction.SUSPEND,
    "suspend": InstallationAction.SUSPEND,
    "unsuspend": InstallationAction.UNSUSPEND,
}
"""``deleted`` suspends instead of removing: PRs and the insert-only audit log reference it."""

REPOSITORIES_ACTIONS = frozenset({"added", "removed"})


class _Payload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class _Account(_Payload):
    login: str
    type: str


class _Installation(_Payload):
    id: int
    account: _Account


class _Repository(_Payload):
    id: int
    full_name: str


class _InstallationWebhook(_Payload):
    action: str
    installation: _Installation
    repositories: list[_Repository] = []
    repositories_added: list[_Repository] = []
    repositories_removed: list[_Repository] = []


class RepoSummary(_Payload):
    """A repository as an installation event names it."""

    repo_id: RepoId
    full_name: str


class InstallationEvent(_Payload):
    """One installation change to apply, already checked.

    Attributes:
        action: What happens to the installation row.
        installation_id: The tenant.
        account_login: The account the App is installed on (refreshed on every event).
        account_type: ``Organization`` or ``User``.
        repos_enabled: Repos to create or re-enable.
        repos_disabled: Repos to switch off; Heimdall stops triaging their PRs.
    """

    action: InstallationAction
    installation_id: InstallationId
    account_login: str
    account_type: str
    repos_enabled: tuple[RepoSummary, ...] = ()
    repos_disabled: tuple[RepoId, ...] = ()


def parse_installation_event(kind: str, payload: Mapping[str, object]) -> InstallationEvent | None:
    """Read an ``installation`` or ``installation_repositories`` webhook.

    Args:
        kind: The ``X-GitHub-Event`` header.
        payload: The webhook body, already signature-checked.

    Returns:
        The change to apply, or ``None`` for events and actions tenancy ignores.

    Raises:
        WebhookPayloadInvalid: a handled event lacks a field tenancy needs.
    """
    action = payload.get("action")
    if not _is_handled(kind, action):
        return None
    try:
        webhook = _InstallationWebhook.model_validate(payload)
    except ValidationError as error:
        raise WebhookPayloadInvalid(f"{kind} webhook is missing {_missing(error)}") from None
    if kind == INSTALLATION:
        return _from_installation(webhook)
    return _from_repositories(webhook)


def _is_handled(kind: str, action: object) -> bool:
    if kind == INSTALLATION:
        return action in INSTALLATION_ACTIONS
    return kind == INSTALLATION_REPOSITORIES and action in REPOSITORIES_ACTIONS


def _from_installation(webhook: _InstallationWebhook) -> InstallationEvent:
    action = INSTALLATION_ACTIONS[webhook.action]
    enabled = webhook.repositories if webhook.action == "created" else []
    return _event(webhook, action, enabled=enabled, disabled=[])


def _from_repositories(webhook: _InstallationWebhook) -> InstallationEvent:
    return _event(
        webhook,
        InstallationAction.UPSERT,
        enabled=webhook.repositories_added,
        disabled=webhook.repositories_removed,
    )


def _event(
    webhook: _InstallationWebhook,
    action: InstallationAction,
    *,
    enabled: list[_Repository],
    disabled: list[_Repository],
) -> InstallationEvent:
    return InstallationEvent(
        action=action,
        installation_id=InstallationId(webhook.installation.id),
        account_login=webhook.installation.account.login,
        account_type=webhook.installation.account.type,
        repos_enabled=tuple(
            RepoSummary(repo_id=RepoId(repo.id), full_name=repo.full_name) for repo in enabled
        ),
        repos_disabled=tuple(RepoId(repo.id) for repo in disabled),
    )


def _missing(error: ValidationError) -> str:
    """Name the first bad field, for example ``installation.id``, without echoing values."""
    location = error.errors()[0]["loc"]
    return ".".join(str(part) for part in location)
