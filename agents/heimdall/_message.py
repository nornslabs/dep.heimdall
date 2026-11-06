"""The room message for a finished Heimdall run, rendered from its validated result."""

from typing import cast

from agents.heimdall.schemas import DeskResult

EMPTY_EXPLANATION_MESSAGE = "Heimdall: I have nothing to add that is about the code."

# One colour per action, so a room can tell the outcome at a glance.
_COLOR_FOR_ACTION: dict[str, str] = {
    "card": "#16a34a",
    "draft": "#2563eb",
    "mechanical_task": "#ca8a04",
    "explanation": "#6b7280",
}

_TITLE_FOR_ACTION: dict[str, str] = {
    "card": "Card ready",
    "draft": "Draft ready",
    "mechanical_task": "Task created",
    "explanation": "Heimdall's take",
}


def render_desk_result(result: DeskResult, saved_message: str | None = None) -> str:
    """Render the room message from a validated desk result.

    An explanation is the one case where the agent's own words reach the room, and they are
    the validated, length-capped field. The other actions point at work already saved by
    Heimdall's tenant-scoped tools, so the trusted text for that work (the approval message
    or the mechanical-task block) is passed in rather than taken from the model's reply.

    Args:
        result: The submission that passed schema validation and the id check.
        saved_message: Trusted text for the saved card, draft or task.

    Returns:
        The room message.

    Raises:
        ValueError: the action saves an artifact but no trusted message was passed.
    """
    if result.action == "explanation":
        if not result.explanation:
            return EMPTY_EXPLANATION_MESSAGE
        return f"Heimdall: {result.explanation}"
    if not saved_message:
        raise ValueError("saved_message is required unless the action is an explanation")
    return saved_message


def render_desk_result_rich(
    result: DeskResult,
    saved_message: str | None,
    *,
    pr_url: str,
    pr_number: int,
    approve_url: str | None,
    reject_url: str | None,
    rerun_url: str | None,
    view_diff_url: str,
) -> dict[str, object]:
    """Render a finished desk run as a Mattermost message with rich attachments.

    The fallback ``message`` is the same trusted text the plain reply uses, so rooms that do
    not render attachments still get the full answer. Buttons call the bridge webhook and are
    left out entirely when their URL is None, so a deployment without the webhook (Phase 4)
    gets a clean markdown message instead of broken links.

    Args:
        result: The validated desk result.
        saved_message: Trusted text for the saved card, draft or task; None for an
            explanation.
        pr_url: Link to the pull request on GitHub.
        pr_number: The pull request number, shown in the attachment title.
        approve_url: Bridge webhook URL that approves the saved draft, or None.
        reject_url: Bridge webhook URL that requests changes, or None.
        rerun_url: Bridge webhook URL that re-runs the checks, or None.
        view_diff_url: Link to the PR's diff on GitHub.

    Returns:
        ``{"message": <fallback text>, "attachments": [<one Mattermost attachment>]}``.

    Raises:
        ValueError: the action saves an artifact but no trusted message was passed.
    """
    if result.action != "explanation" and not saved_message:
        raise ValueError("saved_message is required unless the action is an explanation")

    title = f"PR #{pr_number} — {_TITLE_FOR_ACTION[result.action]}"
    color = _COLOR_FOR_ACTION[result.action]

    fields: list[dict[str, object]] = []
    if saved_message:
        fields.append({"title": "What was saved", "value": saved_message, "short": False})

    actions: list[dict[str, object]] = []
    if result.action in ("card", "draft"):
        if approve_url is not None:
            actions.append({"name": "Approve", "integration": {"url": approve_url}})
        if reject_url is not None:
            actions.append({"name": "Request Changes", "integration": {"url": reject_url}})
    actions.append({"name": "View Diff", "integration": {"url": view_diff_url}})
    if rerun_url is not None:
        actions.append({"name": "Re-run Tests", "integration": {"url": rerun_url}})

    attachment: dict[str, object] = {
        "title": title,
        "title_link": pr_url,
        "color": color,
        "fields": fields,
        "actions": actions,
    }
    body_text = saved_message or result.explanation or ""
    if body_text:
        attachment["text"] = body_text

    return {
        "message": render_desk_result(result, saved_message),
        "attachments": [attachment],
    }


def _artifact_id(result: DeskResult) -> str | None:
    """Return the id of the artifact this result saved, or None for an explanation."""
    return {
        "card": result.card_id,
        "draft": result.draft_id,
        "mechanical_task": result.task_id,
    }.get(result.action)


def rich_attachments_for_result(
    result: DeskResult,
    saved_message: str | None,
    *,
    webhook_url: str | None,
    pr_url: str,
    pr_number: int,
    rerun_pr_id: str,
    view_diff_url: str,
) -> tuple[str, list[dict[str, object]] | None]:
    """Return ``(fallback message, attachments)`` for one desk result's room reply.

    Without a configured bridge webhook URL there are no buttons, so the caller posts the
    plain markdown message and no attachments. With a webhook URL the attachments carry the
    Approve / Request Changes / View Diff / Re-run Tests buttons whose integration URLs call
    the bridge (Phase 4); until that webhook exists the buttons are dead links, which is why
    the URL is opt-in.

    Args:
        result: The validated desk result.
        saved_message: Trusted text for the saved card, draft or task; None for an
            explanation.
        webhook_url: ``HEIMDALL_BRIDGE_WEBHOOK_URL``, or None to render plain markdown only.
        pr_url: Link to the pull request on GitHub (title link of the attachment).
        pr_number: The pull request number, shown in the attachment title.
        rerun_pr_id: The PR id the Re-run Tests button targets.
        view_diff_url: Link to the PR's diff on GitHub.

    Returns:
        The fallback message text, plus the attachment list when a webhook URL is configured
        and the result saved an artifact, else None.
    """
    if webhook_url is None or saved_message is None or result.action == "explanation":
        return render_desk_result(result, saved_message), None

    artifact_id = _artifact_id(result)
    approve_url = (
        f"{webhook_url}/approve/{artifact_id}"
        if artifact_id is not None and result.action in ("card", "draft")
        else None
    )
    reject_url = (
        f"{webhook_url}/reject/{artifact_id}"
        if artifact_id is not None and result.action in ("card", "draft")
        else None
    )
    rich = render_desk_result_rich(
        result,
        saved_message,
        pr_url=pr_url,
        pr_number=pr_number,
        approve_url=approve_url,
        reject_url=reject_url,
        rerun_url=f"{webhook_url}/rerun/{rerun_pr_id}",
        view_diff_url=view_diff_url,
    )
    return cast(str, rich["message"]), cast(list[dict[str, object]], rich["attachments"])
