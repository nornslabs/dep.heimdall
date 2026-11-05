"""The room message that tells maintainers a delivery gave up."""


def render_dead_alert(outbox_id: str, channel: str, kind: str, last_error: str | None) -> str:
    """Say in plain words that a queued message could not be delivered.

    The dead message's own content is left out: it can quote PR text.

    Args:
        outbox_id: Id of the dead outbox row, for finding it in the database.
        channel: Where the message was going (``github`` or ``switch``).
        kind: What the message was, for example ``pr.comment``.
        last_error: Type name of the final failure, if one was recorded.

    Returns:
        A short message for the room.
    """
    reason = last_error or "unknown error"
    return (
        f"Heimdall could not deliver a `{kind}` message to {channel} and has stopped retrying "
        f"({reason}). Outbox id: {outbox_id}."
    )


_ACTION_WORDS = {
    "pr_close": "close this pull request",
    "pr_reopen": "reopen this pull request",
    "pr_merge": "merge this pull request",
}
_DEFAULT_ACTION_WORDS = "post that comment"


def render_action_alert(action: str) -> str:
    """Say in plain words that GitHub did not take an approved action, and what to do.

    GitHub's own reply is left out: it can quote PR content. The usual causes are a PR that
    changed after it was approved, required checks that have not passed, or missing
    permissions, so the advice is to look at the PR and ask again.

    Args:
        action: The approved draft's kind, such as ``pr_merge``.

    Returns:
        A short, friendly message for the PR's thread.
    """
    what = _ACTION_WORDS.get(action, _DEFAULT_ACTION_WORDS)
    return (
        f"GitHub wouldn't let me {what}. The PR may have changed since you approved it, or it "
        "may be waiting on checks or permissions. Have a look, then ask me again."
    )
