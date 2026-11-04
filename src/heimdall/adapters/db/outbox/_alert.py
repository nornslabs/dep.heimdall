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
