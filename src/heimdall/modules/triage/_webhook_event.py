"""Translate supported GitHub pull request payloads into state machine values."""

from collections.abc import Mapping
from typing import cast

from heimdall.modules.triage.domain import EventKind, Facts, PrEvent
from heimdall.shared.ids import RepoId


def pull_request_event(
    delivery_id: str,
    event_name: str,
    action: str | None,
    payload: Mapping[str, object],
) -> PrEvent | None:
    """Validate a supported pull request webhook without trusting its content.

    Args:
        delivery_id: GitHub delivery identifier.
        event_name: GitHub event header value.
        action: GitHub action in the payload.
        payload: Parsed event body, treated only as data.

    Returns:
        A typed PR event, or None for unsupported or malformed payloads.
    """
    if event_name != "pull_request" or action not in _ACTIONS:
        return None
    repository = payload.get("repository")
    pull_request = payload.get("pull_request")
    if not isinstance(repository, Mapping) or not isinstance(pull_request, Mapping):
        return None
    repo_values = cast(Mapping[str, object], repository)
    pr_values = cast(Mapping[str, object], pull_request)
    repo_id = repo_values.get("id")
    full_name = repo_values.get("full_name")
    number = pr_values.get("number")
    user = pr_values.get("user")
    head = pr_values.get("head")
    base = pr_values.get("base")
    if (
        not isinstance(repo_id, int)
        or not isinstance(full_name, str)
        or not isinstance(number, int)
    ):
        return None
    if not isinstance(user, Mapping):
        return None
    user_values = cast(Mapping[str, object], user)
    author = user_values.get("login")
    if not isinstance(author, str):
        return None
    if not isinstance(head, Mapping) or not isinstance(base, Mapping):
        return None
    head_values = cast(Mapping[str, object], head)
    base_values = cast(Mapping[str, object], base)
    head_sha = head_values.get("sha")
    base_sha = base_values.get("sha")
    base_ref = base_values.get("ref")
    if (
        not isinstance(head_sha, str)
        or not isinstance(base_sha, str)
        or not isinstance(base_ref, str)
        or not base_ref
    ):
        return None
    return PrEvent(
        event_id=delivery_id,
        repo_id=RepoId(repo_id),
        repo_full_name=full_name,
        number=number,
        author=author,
        head_sha=head_sha,
        base_sha=base_sha,
        base_ref=base_ref,
        kind=_ACTIONS[action],
        facts=Facts(),
    )


_ACTIONS = {
    "opened": EventKind.PR_OPENED,
    "synchronize": EventKind.PR_SYNCHRONIZE,
    "edited": EventKind.PR_EDITED,
    "closed": EventKind.PR_CLOSED,
}
