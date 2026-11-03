"""Backoff for model calls: retry only AiUnavailable, honouring Retry-After within a cap."""

from collections.abc import Awaitable, Callable

from heimdall.shared.errors import AiUnavailable
from heimdall.shared.logging import get_logger

RETRY_DELAYS_SECONDS = (2.0, 8.0, 30.0)
MAX_RETRY_AFTER_SECONDS = 60.0

Sleep = Callable[[float], Awaitable[None]]

log = get_logger(__name__)


async def with_retry[T](
    send: Callable[[], Awaitable[T]],
    sleep: Sleep,
    delays: tuple[float, ...] = RETRY_DELAYS_SECONDS,
) -> T:
    """Call ``send``, retrying after each delay while the provider is unavailable.

    Args:
        send: One request attempt; called once plus once per delay at most.
        sleep: Async sleep, injectable so tests do not wait.
        delays: Wait before each retry, used when the provider gives no Retry-After.

    Returns:
        The first successful result.

    Raises:
        AiUnavailable: every attempt failed with an outage.
        HeimdallError: any non-retryable error, raised on the first attempt that hits it.
    """
    for attempt, delay in enumerate(delays, start=1):
        try:
            return await send()
        except AiUnavailable as error:
            wait = _wait_seconds(error, delay)
            log.warning("ai_request_retry", attempt=attempt, wait_seconds=wait)
            await sleep(wait)
    return await send()


def _wait_seconds(error: AiUnavailable, delay: float) -> float:
    """Prefer the provider's Retry-After, capped so one header cannot stall an agent."""
    if error.retry_after is None:
        return delay
    return min(max(error.retry_after, 0.0), MAX_RETRY_AFTER_SECONDS)
