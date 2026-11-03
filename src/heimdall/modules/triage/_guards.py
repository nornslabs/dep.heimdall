"""Guards: the conditions some transitions need, each with the reason it reports on failure."""

from collections.abc import Callable
from dataclasses import dataclass

from heimdall.modules.triage._states import Facts, RejectReason


@dataclass(frozen=True)
class Guard:
    """A named condition on ``Facts`` and the rejection reason when it does not hold."""

    check: Callable[[Facts], bool]
    reason: RejectReason


def _repo_enabled(facts: Facts) -> bool:
    return facts.repo_enabled


def _intake_passed(facts: Facts) -> bool:
    return facts.intake_passed


def _intake_failed(facts: Facts) -> bool:
    return not facts.intake_passed


def _both_checks_done(facts: Facts) -> bool:
    return facts.repro_done and facts.review_done


def _one_check_pending(facts: Facts) -> bool:
    return not (facts.repro_done and facts.review_done)


def _sender_is_maintainer(facts: Facts) -> bool:
    return facts.sender_is_maintainer


def _draft_hash_matches(facts: Facts) -> bool:
    return facts.draft_hash_matches


def _category_auto_suggest(facts: Facts) -> bool:
    return facts.category_auto_suggest


# Paired guards (passed/failed, both/one) always let one of their two rows through, so their
# reason is never reported; NOT_ALLOWED_FROM_STATE is only a placeholder there.
REPO_ENABLED = Guard(_repo_enabled, RejectReason.REPO_NOT_ENABLED)
INTAKE_PASSED = Guard(_intake_passed, RejectReason.NOT_ALLOWED_FROM_STATE)
INTAKE_FAILED = Guard(_intake_failed, RejectReason.NOT_ALLOWED_FROM_STATE)
BOTH_CHECKS_DONE = Guard(_both_checks_done, RejectReason.NOT_ALLOWED_FROM_STATE)
ONE_CHECK_PENDING = Guard(_one_check_pending, RejectReason.NOT_ALLOWED_FROM_STATE)
SENDER_IS_MAINTAINER = Guard(_sender_is_maintainer, RejectReason.NOT_A_MAINTAINER)
DRAFT_HASH_MATCHES = Guard(_draft_hash_matches, RejectReason.DRAFT_HASH_CHANGED)
CATEGORY_AUTO_SUGGEST = Guard(_category_auto_suggest, RejectReason.CATEGORY_NOT_AUTO_SUGGEST)
