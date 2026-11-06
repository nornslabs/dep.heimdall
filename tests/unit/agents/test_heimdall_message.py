"""Tests for rendering a finished Heimdall run as a rich Mattermost reply."""

from typing import cast

from agents.heimdall._message import (
    render_desk_result_rich,
    rich_attachments_for_result,
)
from agents.heimdall.schemas import DeskAction, DeskResult

WEBHOOK = "https://bridge.example.com/switch/command"
PR_URL = "https://github.com/octo-org/r1/pull/42"
VIEW_DIFF = "https://github.com/octo-org/r1/pull/42/files"


def _card() -> DeskResult:
    return DeskResult(action="card", card_id="card-123")


def _draft() -> DeskResult:
    return DeskResult(action="draft", draft_id="draft-123")


def _task() -> DeskResult:
    return DeskResult(action="mechanical_task", task_id="task-123")


def _explanation() -> DeskResult:
    return DeskResult(action="explanation", explanation="The fix looks right to me.")


def _attachment(rich: dict[str, object]) -> dict[str, object]:
    attachments = cast(list[dict[str, object]], rich["attachments"])
    assert len(attachments) == 1
    return attachments[0]


def _action_names(attachment: dict[str, object]) -> list[str]:
    return [
        cast(str, action["name"]) for action in cast(list[dict[str, object]], attachment["actions"])
    ]


def _action_urls(attachment: dict[str, object]) -> list[str]:
    urls: list[str] = []
    for action in cast(list[dict[str, object]], attachment["actions"]):
        integration = cast(dict[str, object], action["integration"])
        urls.append(cast(str, integration["url"]))
    return urls


def test_card_renders_rich_attachment_with_approve_and_reject_buttons() -> None:
    rich = render_desk_result_rich(
        _card(),
        "Approval: please merge this draft",
        pr_url=PR_URL,
        pr_number=42,
        approve_url=f"{WEBHOOK}/approve/card-123",
        reject_url=f"{WEBHOOK}/reject/card-123",
        rerun_url=f"{WEBHOOK}/rerun/pr-1",
        view_diff_url=VIEW_DIFF,
    )

    assert rich["message"] == "Approval: please merge this draft"
    attachment = _attachment(rich)
    assert attachment["title"] == "PR #42 — Card ready"
    assert attachment["title_link"] == PR_URL
    assert attachment["color"] == "#16a34a"
    assert attachment["text"] == "Approval: please merge this draft"
    assert attachment["fields"] == [
        {"title": "What was saved", "value": "Approval: please merge this draft", "short": False}
    ]
    assert _action_names(attachment) == ["Approve", "Request Changes", "View Diff", "Re-run Tests"]
    assert _action_urls(attachment) == [
        f"{WEBHOOK}/approve/card-123",
        f"{WEBHOOK}/reject/card-123",
        VIEW_DIFF,
        f"{WEBHOOK}/rerun/pr-1",
    ]


def test_draft_renders_the_same_buttons_on_its_draft_id() -> None:
    rich = render_desk_result_rich(
        _draft(),
        "Draft: fix the failing test",
        pr_url=PR_URL,
        pr_number=42,
        approve_url=f"{WEBHOOK}/approve/draft-123",
        reject_url=f"{WEBHOOK}/reject/draft-123",
        rerun_url=None,
        view_diff_url=VIEW_DIFF,
    )

    attachment = _attachment(rich)
    assert attachment["title"] == "PR #42 — Draft ready"
    assert attachment["color"] == "#2563eb"
    assert _action_names(attachment) == ["Approve", "Request Changes", "View Diff"]
    assert _action_urls(attachment)[0] == f"{WEBHOOK}/approve/draft-123"


def test_mechanical_task_renders_without_approve_buttons() -> None:
    rich = render_desk_result_rich(
        _task(),
        "Mechanical task created: backport to 2.4",
        pr_url=PR_URL,
        pr_number=42,
        approve_url=f"{WEBHOOK}/approve/task-123",
        reject_url=f"{WEBHOOK}/reject/task-123",
        rerun_url=f"{WEBHOOK}/rerun/pr-1",
        view_diff_url=VIEW_DIFF,
    )

    attachment = _attachment(rich)
    assert attachment["title"] == "PR #42 — Task created"
    assert attachment["color"] == "#ca8a04"
    assert _action_names(attachment) == ["View Diff", "Re-run Tests"]


def test_explanation_renders_the_explanation_without_save_buttons() -> None:
    rich = render_desk_result_rich(
        _explanation(),
        None,
        pr_url=PR_URL,
        pr_number=42,
        approve_url=f"{WEBHOOK}/approve/x",
        reject_url=f"{WEBHOOK}/reject/x",
        rerun_url=f"{WEBHOOK}/rerun/pr-1",
        view_diff_url=VIEW_DIFF,
    )

    assert rich["message"] == "Heimdall: The fix looks right to me."
    attachment = _attachment(rich)
    assert attachment["title"] == "PR #42 — Heimdall's take"
    assert attachment["color"] == "#6b7280"
    assert attachment["text"] == "The fix looks right to me."
    assert _action_names(attachment) == ["View Diff", "Re-run Tests"]


def test_missing_saved_message_raises_for_actions_that_save() -> None:
    for action, field in (
        ("card", "card_id"),
        ("draft", "draft_id"),
        ("mechanical_task", "task_id"),
    ):
        result = DeskResult(action=cast(DeskAction, action), **{field: "id-1"})
        try:
            render_desk_result_rich(
                result,
                None,
                pr_url=PR_URL,
                pr_number=42,
                approve_url=f"{WEBHOOK}/approve/x",
                reject_url=f"{WEBHOOK}/reject/x",
                rerun_url=None,
                view_diff_url=VIEW_DIFF,
            )
        except ValueError:
            continue
        raise AssertionError(f"{action} without saved_message should raise")


def test_rich_attachments_for_result_without_webhook_is_plain_markdown() -> None:
    message, attachments = rich_attachments_for_result(
        _card(),
        "Approval: please merge this draft",
        webhook_url=None,
        pr_url=PR_URL,
        pr_number=42,
        rerun_pr_id="pr-1",
        view_diff_url=VIEW_DIFF,
    )

    assert message == "Approval: please merge this draft"
    assert attachments is None


def test_rich_attachments_for_result_with_webhook_builds_button_urls() -> None:
    _message, attachments = rich_attachments_for_result(
        _card(),
        "Approval: please merge this draft",
        webhook_url=WEBHOOK,
        pr_url=PR_URL,
        pr_number=42,
        rerun_pr_id="pr-1",
        view_diff_url=VIEW_DIFF,
    )

    assert attachments is not None
    attachment = _attachment({"message": "", "attachments": attachments})
    urls = _action_urls(attachment)
    assert f"{WEBHOOK}/approve/card-123" in urls
    assert f"{WEBHOOK}/reject/card-123" in urls
    assert f"{WEBHOOK}/rerun/pr-1" in urls
    assert VIEW_DIFF in urls
