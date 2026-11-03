"""Use cases of the intake module."""

from heimdall.modules.intake._run import run_intake
from heimdall.modules.intake._stale import mark_results_stale

__all__ = ["mark_results_stale", "run_intake"]
