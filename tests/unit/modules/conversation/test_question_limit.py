"""The question limit: who is admitted, who gets one notice, and who is left alone."""

from datetime import UTC, datetime, timedelta

from heimdall.modules.conversation.domain import (
    MAX_QUESTIONS_PER_WINDOW,
    QUESTION_WINDOW,
    Admitted,
    Limited,
    decide_question,
)

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


def _answers(count: int, *, minutes_ago: int = 5) -> list[datetime]:
    """``count`` answered questions, one second apart, ending ``minutes_ago`` before NOW."""
    last = NOW - timedelta(minutes=minutes_ago)
    return [last - timedelta(seconds=count - 1 - index) for index in range(count)]


def test_question_is_admitted_under_the_limit() -> None:
    decision = decide_question(_answers(MAX_QUESTIONS_PER_WINDOW - 1), False, NOW)

    assert decision == Admitted()


def test_first_question_is_admitted_with_no_history() -> None:
    assert decide_question([], False, NOW) == Admitted()


def test_question_over_limit_sends_one_notice() -> None:
    decision = decide_question(_answers(MAX_QUESTIONS_PER_WINDOW), False, NOW)

    assert isinstance(decision, Limited)
    assert decision.send_notice is True


def test_later_questions_in_window_stay_silent() -> None:
    decision = decide_question(_answers(MAX_QUESTIONS_PER_WINDOW), True, NOW)

    assert isinstance(decision, Limited)
    assert decision.send_notice is False


def test_retry_after_is_oldest_answer_plus_window() -> None:
    answers = _answers(MAX_QUESTIONS_PER_WINDOW)

    decision = decide_question(answers, False, NOW)

    assert isinstance(decision, Limited)
    assert decision.retry_after == min(answers) + QUESTION_WINDOW


def test_old_answers_fall_out_of_the_window() -> None:
    stale = _answers(MAX_QUESTIONS_PER_WINDOW, minutes_ago=61)

    assert decide_question(stale, False, NOW) == Admitted()


def test_answer_exactly_one_window_old_no_longer_counts() -> None:
    answers = [NOW - QUESTION_WINDOW, *_answers(MAX_QUESTIONS_PER_WINDOW - 1)]

    assert decide_question(answers, False, NOW) == Admitted()


def test_retry_after_ignores_answers_outside_the_window() -> None:
    stale = [NOW - QUESTION_WINDOW - timedelta(minutes=10)]
    fresh = _answers(MAX_QUESTIONS_PER_WINDOW)

    decision = decide_question([*stale, *fresh], False, NOW)

    assert isinstance(decision, Limited)
    assert decision.retry_after == min(fresh) + QUESTION_WINDOW
