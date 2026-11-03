"""Pure reproduction run values and verdict rules."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

MAX_REPRO_RUNS = 3
REPRO_TIMEOUT_SECONDS = 30 * 60


class ReproStatus(StrEnum):
    """Lifecycle status stored for one reproduction attempt."""

    QUEUED = "queued"
    RUNNING = "running"
    FINISHED = "finished"
    TIMED_OUT = "timed_out"
    STALE = "stale"


class Verdict(StrEnum):
    """What paired test runs say about the proposed fix."""

    FIX_CONFIRMED = "fix_confirmed"
    FIX_FAILED = "fix_failed"
    NOT_REPRODUCED = "not_reproduced"


@dataclass(frozen=True)
class RunOutcome:
    """Structured result from running the configured command once.

    Attributes:
        passed: Whether all tests passed.
        failures: Short test identifiers for failed checks.
        duration_seconds: Runtime reported by the workflow.
    """

    passed: bool
    failures: tuple[str, ...] = ()
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class ReproRunRef:
    """Stable run identity returned across the repro module boundary."""

    id: UUID
    attempt: int
    head_sha: str
    status: ReproStatus


@dataclass(frozen=True)
class ReproResult:
    """Final paired test outputs and derived verdict."""

    run_id: UUID
    status: ReproStatus
    verdict: Verdict | None


@dataclass(frozen=True)
class ReproRunDetails:
    """Tenant-scoped reproduction state returned to callers."""

    id: UUID
    pr_id: UUID
    head_sha: str
    attempt: int
    status: ReproStatus
    with_fix: dict[str, object] | None
    without_fix: dict[str, object] | None
    verdict: Verdict | None


def derive_verdict(with_fix: RunOutcome, without_fix: RunOutcome) -> Verdict:
    """Compare baseline and patched test outcomes to classify the proposed fix.

    Args:
        with_fix: Test result from the PR commit.
        without_fix: Test result from the base commit.

    Returns:
        Whether the fix makes the failing behavior pass, fails itself, or does not reproduce.
    """
    if not with_fix.passed:
        return Verdict.FIX_FAILED
    if not without_fix.passed:
        return Verdict.FIX_CONFIRMED
    return Verdict.NOT_REPRODUCED


__all__ = [
    "MAX_REPRO_RUNS",
    "REPRO_TIMEOUT_SECONDS",
    "ReproResult",
    "ReproRunDetails",
    "ReproRunRef",
    "ReproStatus",
    "RunOutcome",
    "Verdict",
    "derive_verdict",
]
