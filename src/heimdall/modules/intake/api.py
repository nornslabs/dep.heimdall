"""Public interface of the intake module.

Other modules may import only from this file.
"""

from heimdall.modules.intake._rules import evaluate_rules
from heimdall.modules.intake.domain import IntakeFacts, RuleName, RuleResult
from heimdall.modules.intake.service import mark_results_stale, run_intake

__all__ = [
    "IntakeFacts",
    "RuleName",
    "RuleResult",
    "evaluate_rules",
    "mark_results_stale",
    "run_intake",
]
