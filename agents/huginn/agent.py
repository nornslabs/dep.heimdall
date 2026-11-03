"""Huginn's bounded reproduction loop and deterministic verdict message."""

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID

from heimdall.modules.repro.api import (
    MAX_REPRO_RUNS,
    REPRO_TIMEOUT_SECONDS,
    ReproRunDetails,
    ReproStatus,
    Verdict,
)

POLL_INTERVAL_SECONDS = 5
MAX_TURNS = REPRO_TIMEOUT_SECONDS // POLL_INTERVAL_SECONDS
MAX_FAILURE_DETAILS = 5
MAX_FAILURE_LENGTH = 300


class HuginnTools(Protocol):
    """Tenant-scoped reproduction operations Huginn may call."""

    async def start_repro(
        self, installation_id: int, pr_id: str, head_sha: str, test_cmd: str
    ) -> dict[str, str | int]:
        """Queue one configured reproduction attempt."""
        ...

    async def get_repro(self, installation_id: int, run_id: str) -> dict[str, object]:
        """Read a persisted attempt and its paired test results."""
        ...


@dataclass(frozen=True)
class HuginnResult:
    """Bounded reproduction attempts and their maintainer-readable verdict."""

    run_ids: tuple[UUID, ...]
    status: ReproStatus
    verdict: Verdict | None
    message: str


async def run_huginn(
    tools: HuginnTools,
    installation_id: int,
    pr_id: UUID,
    head_sha: str,
    test_cmd: str,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> HuginnResult:
    """Start up to three paired runs, poll saved results, and report one verdict.

    Args:
        tools: Tenant-scoped reproduction tool client.
        installation_id: Tenant owning the pull request.
        pr_id: Triage pull request identifier.
        head_sha: Exact PR commit under test.
        test_cmd: Test command from validated repository configuration.
        sleep: Injectable delay for bounded status polling.

    Returns:
        All started run ids, aggregate status, majority verdict, and one report.
    """
    run_ids = await _start_attempts(tools, installation_id, pr_id, head_sha, test_cmd)
    current: dict[UUID, ReproRunDetails | None] = {run_id: None for run_id in run_ids}
    for turn in range(MAX_TURNS):
        for run_id in run_ids:
            latest = current[run_id]
            if latest is not None and latest.status in _TERMINAL_STATUSES:
                continue
            details = _parse_run(await tools.get_repro(installation_id, str(run_id)))
            if details.id != run_id or details.pr_id != pr_id or details.head_sha != head_sha:
                raise ValueError("MCP get_repro returned a run for a different PR head")
            current[run_id] = details
        if all(run is not None and run.status in _TERMINAL_STATUSES for run in current.values()):
            break
        if turn + 1 < MAX_TURNS:
            await sleep(POLL_INTERVAL_SECONDS)

    saved_runs = tuple(run for run in current.values() if run is not None)
    status = _aggregate_status(saved_runs, len(run_ids))
    verdict = _majority_verdict(saved_runs) if status is ReproStatus.FINISHED else None
    message = format_verdict_message(saved_runs, status, verdict, len(run_ids))
    return HuginnResult(run_ids, status, verdict, message)


_TERMINAL_STATUSES = frozenset({ReproStatus.FINISHED, ReproStatus.TIMED_OUT, ReproStatus.STALE})


async def _start_attempts(
    tools: HuginnTools,
    installation_id: int,
    pr_id: UUID,
    head_sha: str,
    test_cmd: str,
) -> tuple[UUID, ...]:
    """Queue the maximum bounded sample before polling any run."""
    run_ids: list[UUID] = []
    for attempt in range(1, MAX_REPRO_RUNS + 1):
        started = await tools.start_repro(installation_id, str(pr_id), head_sha, test_cmd)
        run_id = _required_uuid(started.get("run_id"))
        if started.get("attempt") != attempt or started.get("head_sha") != head_sha:
            raise ValueError("MCP start_repro returned an unexpected attempt")
        if run_id in run_ids:
            raise ValueError("MCP start_repro returned a duplicate run id")
        run_ids.append(run_id)
    return tuple(run_ids)


def format_verdict_message(
    runs: tuple[ReproRunDetails, ...],
    status: ReproStatus,
    verdict: Verdict | None,
    requested_runs: int,
) -> str:
    """Format the persisted attempt counts and failure evidence once.

    Args:
        runs: Saved attempt records returned by tenant-scoped tools.
        status: Aggregate lifecycle status.
        verdict: Majority verdict when all attempts finished consistently enough.
        requested_runs: Number of attempts started.

    Returns:
        A concise room message grounded in saved test outcomes.
    """
    completed = tuple(run for run in runs if run.status is ReproStatus.FINISHED)
    with_fix_passed = sum(_passed(run.with_fix) for run in completed)
    without_fix_passed = sum(_passed(run.without_fix) for run in completed)
    header = _verdict_label(status, verdict)
    lines = [
        f"Huginn: {header}",
        f"Paired attempts: {len(completed)}/{requested_runs} completed.",
        f"Passed with fix: {with_fix_passed}/{len(completed)}; "
        f"passed without fix: {without_fix_passed}/{len(completed)}.",
    ]
    failures = _failure_details(completed)
    if failures:
        lines.append("Failing assertions or test errors:")
        lines.extend(f"- {failure}" for failure in failures)
    return "\n".join(lines)


def _verdict_label(status: ReproStatus, verdict: Verdict | None) -> str:
    """Describe aggregate state without converting missing data into a verdict."""
    if status is ReproStatus.STALE:
        return "stale; the PR head changed during reproduction."
    if status is ReproStatus.TIMED_OUT:
        return "timed out before all attempts completed."
    if status is not ReproStatus.FINISHED:
        return "still running; no final verdict is available."
    if verdict is None:
        return "mixed results; no majority verdict."
    return {
        Verdict.FIX_CONFIRMED: "fix confirmed by paired test results.",
        Verdict.FIX_FAILED: "fix failed the configured tests.",
        Verdict.NOT_REPRODUCED: "issue not reproduced by the configured tests.",
    }[verdict]


def _aggregate_status(runs: tuple[ReproRunDetails, ...], expected: int) -> ReproStatus:
    """Choose the most cautious aggregate lifecycle state."""
    if any(run.status is ReproStatus.STALE for run in runs):
        return ReproStatus.STALE
    if len(runs) < expected or any(run.status not in _TERMINAL_STATUSES for run in runs):
        return ReproStatus.RUNNING
    if any(run.status is ReproStatus.TIMED_OUT for run in runs):
        return ReproStatus.TIMED_OUT
    return ReproStatus.FINISHED


def _majority_verdict(runs: tuple[ReproRunDetails, ...]) -> Verdict | None:
    """Return a verdict only when completed runs give one strict majority."""
    counts = Counter(run.verdict for run in runs if run.status is ReproStatus.FINISHED)
    counts.pop(None, None)
    if not counts:
        return None
    winner, count = counts.most_common(1)[0]
    if count <= len(runs) // 2:
        return None
    return winner


def _failure_details(runs: tuple[ReproRunDetails, ...]) -> tuple[str, ...]:
    """Collect unique persisted failure lines without copying full test output."""
    failures: list[str] = []
    for run in runs:
        for outcome in (run.with_fix, run.without_fix):
            if outcome is None:
                continue
            raw_failures = outcome.get("failures")
            if not isinstance(raw_failures, list | tuple):
                continue
            for raw_failure in cast(list[object] | tuple[object, ...], raw_failures):
                if not isinstance(raw_failure, str):
                    continue
                failure = raw_failure.strip()[:MAX_FAILURE_LENGTH]
                if failure and failure not in failures:
                    failures.append(failure)
                if len(failures) == MAX_FAILURE_DETAILS:
                    return tuple(failures)
    return tuple(failures)


def _passed(outcome: dict[str, object] | None) -> bool:
    """Read the paired result's saved pass flag."""
    return outcome is not None and outcome.get("passed") is True


def _parse_run(value: dict[str, object]) -> ReproRunDetails:
    """Validate a structured MCP response before judging its result."""
    try:
        verdict_value = value.get("verdict")
        verdict = Verdict(str(verdict_value)) if verdict_value is not None else None
        return ReproRunDetails(
            id=UUID(str(value["run_id"])),
            pr_id=UUID(str(value["pr_id"])),
            head_sha=str(value["head_sha"]),
            attempt=_int(value["attempt"]),
            status=ReproStatus(str(value["status"])),
            with_fix=_outcome(value.get("with_fix")),
            without_fix=_outcome(value.get("without_fix")),
            verdict=verdict,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("MCP get_repro returned an invalid run record") from error


def _outcome(value: object) -> dict[str, object] | None:
    """Accept only mapping-like persisted test outputs."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("repro outcome must be an object")
    outcome = cast(dict[object, object], value)
    return {str(key): item for key, item in outcome.items()}


def _required_uuid(value: object) -> UUID:
    """Parse an attempt id from the reproduction tool response."""
    try:
        if not isinstance(value, str):
            raise ValueError
        return UUID(value)
    except ValueError as error:
        raise ValueError("MCP start_repro returned an invalid run id") from error


def _int(value: object) -> int:
    """Accept a JSON integer, rejecting booleans and other types."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("expected an integer")
    return value
