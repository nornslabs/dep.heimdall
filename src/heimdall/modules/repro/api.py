"""Public interface of the repro module.

Other modules may import only from this file.
"""

from heimdall.modules.repro.domain import (
    MAX_REPRO_RUNS,
    REPRO_TIMEOUT_SECONDS,
    ReproResult,
    ReproRunDetails,
    ReproRunRef,
    ReproStatus,
    RunOutcome,
    Verdict,
    derive_verdict,
)
from heimdall.modules.repro.service import (
    get_run,
    latest_run_id,
    mark_runs_stale,
    record_result,
    start_run,
    timeout_run,
)

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
    "get_run",
    "latest_run_id",
    "mark_runs_stale",
    "record_result",
    "start_run",
    "timeout_run",
]
