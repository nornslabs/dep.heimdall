"""Pure rules for how many questions one sender may put to one agent on one PR (no I/O)."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from heimdall.shared.agents import AgentName

MAX_QUESTIONS_PER_WINDOW = 20
"""Most questions one sender gets answered by one agent on one PR in one window."""

QUESTION_WINDOW = timedelta(hours=1)
"""How far back answered questions count; the window rolls with the clock."""


class QuestionOutcome(StrEnum):
    """What became of a question, as it is saved."""

    ANSWERED = "answered"
    LIMITED = "limited"
    """Over the limit, and this refusal carried the one notice for its window."""
    LIMITED_SILENT = "limited_silent"
    """Over the limit after the notice had gone out; nothing is sent."""


@dataclass(frozen=True)
class QuestionScope:
    """Whose allowance a question is counted against.

    Attributes:
        agent: The agent being asked.
        sender: The sender's login; compared ignoring case.
        pr_id: The PR whose thread the question is in, or None outside a PR thread.
        room_id: The Switch room, used as the scope when there is no PR.
    """

    agent: AgentName
    sender: str
    pr_id: uuid.UUID | None
    room_id: str


@dataclass(frozen=True)
class Admitted:
    """The question may be answered."""


@dataclass(frozen=True)
class Limited:
    """The sender is over the limit and the question must not reach the model.

    Attributes:
        retry_after: When the oldest counted answer leaves the window, so a question asked
            then is admitted again.
        send_notice: True for the first refusal in the window, which gets a short reply;
            False for every later one, which gets none.
    """

    retry_after: datetime
    send_notice: bool


type QuestionDecision = Admitted | Limited


def decide_question(
    answered_at: Sequence[datetime], notice_sent_in_window: bool, now: datetime
) -> QuestionDecision:
    """Decide whether a sender's next question to an agent on a PR may be answered.

    Args:
        answered_at: When this sender's earlier questions to this agent on this PR were
            answered. Entries outside the window are ignored.
        notice_sent_in_window: Whether a rate-limit notice already went out in the window.
        now: The time of the new question.

    Returns:
        ``Admitted`` while fewer than ``MAX_QUESTIONS_PER_WINDOW`` answers are in the window.
        Otherwise ``Limited``, asking for a notice only if none was sent yet.
    """
    window_start = now - QUESTION_WINDOW
    in_window = [at for at in answered_at if at > window_start]
    if len(in_window) < MAX_QUESTIONS_PER_WINDOW:
        return Admitted()
    return Limited(
        retry_after=min(in_window) + QUESTION_WINDOW,
        send_notice=not notice_sent_in_window,
    )
