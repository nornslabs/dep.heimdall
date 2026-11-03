"""When a failed outbox message is tried again, and when it is given up on."""

from dataclasses import dataclass
from datetime import datetime, timedelta

RETRY_DELAYS = (
    timedelta(seconds=2),
    timedelta(seconds=8),
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
)
"""Wait before each retry; after the last one the message is dead (LLD: 5 attempts)."""


@dataclass(frozen=True)
class Retry:
    """Try the message again at ``at``."""

    at: datetime


@dataclass(frozen=True)
class Dead:
    """Stop trying; the message needs a human."""


def after_failure(attempts_so_far: int, *, retryable: bool, now: datetime) -> Retry | Dead:
    """Decide what happens to a message whose send just failed.

    Args:
        attempts_so_far: Failed attempts before this one.
        retryable: Whether trying again could help (an outage, not a broken rule).
        now: The time of this failure.

    Returns:
        ``Retry`` with the next attempt time, or ``Dead`` when retrying is pointless or
        the retries are used up.
    """
    if not retryable or attempts_so_far >= len(RETRY_DELAYS):
        return Dead()
    return Retry(at=now + RETRY_DELAYS[attempts_so_far])
