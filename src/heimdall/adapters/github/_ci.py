"""How a commit's statuses and check runs add up to one CI state (pure)."""

from collections.abc import Iterable

from heimdall.shared.github import CiState

FAILED_STATUSES = frozenset({"failure", "error"})
FAILED_CONCLUSIONS = frozenset({"failure", "timed_out", "cancelled", "action_required", "stale"})
COMPLETED = "completed"
PENDING_STATUS = "pending"


def combine_ci(statuses: Iterable[str], check_runs: Iterable[tuple[str, str | None]]) -> CiState:
    """Combine commit statuses and check runs: any failure wins, then anything still running.

    Args:
        statuses: Each commit status ``state`` (success, pending, failure, error).
        check_runs: Each check run's ``(status, conclusion)``.

    Returns:
        ``FAILURE``, ``PENDING``, ``SUCCESS``, or ``NONE`` when nothing reported at all.
    """
    states = list(statuses)
    runs = list(check_runs)
    if not states and not runs:
        return CiState.NONE
    if any(state in FAILED_STATUSES for state in states):
        return CiState.FAILURE
    if any(status == COMPLETED and conclusion in FAILED_CONCLUSIONS for status, conclusion in runs):
        return CiState.FAILURE
    if PENDING_STATUS in states or any(status != COMPLETED for status, _ in runs):
        return CiState.PENDING
    return CiState.SUCCESS
