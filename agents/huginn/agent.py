"""Huginn's bounded reproduction tool loop and verdict message."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from heimdall.modules.repro.api import ReproRunDetails, ReproStatus, Verdict

MAX_TURNS = 20
POLL_INTERVAL_SECONDS = 5


class HuginnTools(Protocol):
    """MCP calls Huginn is allowed to make."""

    async def start_repro(
        self, installation_id: int, pr_id: str, head_sha: str, test_cmd: str
    ) -> dict[str, str | int]:
        """Queue one configured reproduction attempt."""
        ...

    async def get_repro(self, installation_id: int, run_id: str) -> dict[str, object]:
        """Read current status and paired outcomes for an attempt."""
        ...


@dataclass(frozen=True)
class HuginnResult:
    """Final state and maintainer-readable reproduction message."""

    run_id: UUID
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
    """Start and monitor one reproduction attempt without exceeding the turn cap.

    Args:
        tools: Scoped MCP client exposing only Huginn's tools.
        installation_id: Tenant owning the PR.
        pr_id: PR row id being reproduced.
        head_sha: Exact PR commit under test.
        test_cmd: Test command resolved from trusted repository configuration.
        sleep: Injectable delay between pending status checks.

    Returns:
        Terminal verdict or a bounded still-running result.
    """
    started = await tools.start_repro(installation_id, str(pr_id), head_sha, test_cmd)
    run_id = UUID(str(started["run_id"]))
    for turn in range(MAX_TURNS):
        details = _parse_run(await tools.get_repro(installation_id, str(run_id)))
        if details.status in {
            ReproStatus.FINISHED,
            ReproStatus.TIMED_OUT,
            ReproStatus.STALE,
        }:
            return HuginnResult(
                run_id,
                details.status,
                details.verdict,
                format_verdict_message(details),
            )
        if turn + 1 < MAX_TURNS:
            await sleep(POLL_INTERVAL_SECONDS)
    pending = details.status
    return HuginnResult(
        run_id,
        pending,
        None,
        f"Reproduction run {run_id} is still {pending.value} after {MAX_TURNS} checks.",
    )


def format_verdict_message(run: ReproRunDetails) -> str:
    """Format a concise report that matches the stored paired test outcomes.

    Args:
        run: Terminal reproduction state.

    Returns:
        Verdict message suitable for a Switch room draft.
    """
    if run.status is ReproStatus.STALE:
        return f"Reproduction run {run.id} is stale because the PR head changed."
    if run.status is ReproStatus.TIMED_OUT:
        return f"Reproduction run {run.id} timed out; no verdict is available."
    if run.status is not ReproStatus.FINISHED or run.verdict is None:
        return f"Reproduction run {run.id} has no completed verdict yet."
    return {
        Verdict.FIX_CONFIRMED: (
            f"Fix confirmed for {run.id}: tests passed with the fix and failed without it."
        ),
        Verdict.FIX_FAILED: (f"Fix failed for {run.id}: tests did not pass with the proposed fix."),
        Verdict.NOT_REPRODUCED: (
            f"Issue not reproduced for {run.id}: tests passed both with and without the fix."
        ),
    }[run.verdict]


def _parse_run(value: dict[str, object]) -> ReproRunDetails:
    """Validate a structured MCP response before using it as a verdict."""
    try:
        verdict_value = value.get("verdict")
        verdict = Verdict(str(verdict_value)) if verdict_value is not None else None
        return ReproRunDetails(
            id=UUID(str(value["run_id"])),
            pr_id=UUID(str(value["pr_id"])),
            head_sha=str(value["head_sha"]),
            attempt=int(value["attempt"]),
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
    return {str(key): item for key, item in value.items()}
