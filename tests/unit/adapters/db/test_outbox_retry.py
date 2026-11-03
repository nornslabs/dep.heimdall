from datetime import UTC, datetime, timedelta

import pytest

from heimdall.adapters.db.outbox._retry import RETRY_DELAYS, Dead, Retry, after_failure

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("attempts_so_far", "delay"),
    [
        (0, timedelta(seconds=2)),
        (1, timedelta(seconds=8)),
        (2, timedelta(seconds=30)),
        (3, timedelta(minutes=2)),
        (4, timedelta(minutes=10)),
    ],
)
def test_retry_waits_longer_after_each_failure(attempts_so_far: int, delay: timedelta) -> None:
    assert after_failure(attempts_so_far, retryable=True, now=NOW) == Retry(at=NOW + delay)


def test_message_is_dead_after_the_last_retry() -> None:
    assert after_failure(len(RETRY_DELAYS), retryable=True, now=NOW) == Dead()


def test_non_retryable_failure_is_dead_at_once() -> None:
    assert after_failure(0, retryable=False, now=NOW) == Dead()
