"""Public conversation interface; the only file other modules import."""

from heimdall.modules.conversation.domain import (
    MAX_QUESTIONS_PER_WINDOW,
    QUESTION_WINDOW,
    Admitted,
    Limited,
    QuestionDecision,
    QuestionScope,
)
from heimdall.modules.conversation.service import admit_question_in_transaction

__all__ = [
    "MAX_QUESTIONS_PER_WINDOW",
    "QUESTION_WINDOW",
    "Admitted",
    "Limited",
    "QuestionDecision",
    "QuestionScope",
    "admit_question_in_transaction",
]
