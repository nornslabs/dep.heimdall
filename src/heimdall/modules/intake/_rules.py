"""Pure intake checks over fetched GitHub facts and the effective repo config."""

import re
from collections.abc import Callable

from heimdall.modules.intake.domain import IntakeFacts, RuleName, RuleResult
from heimdall.modules.tenancy.api import IntakeConfig

LINKED_ISSUE_PATTERN = re.compile(r"\bcloses\s+#\d+\b", re.IGNORECASE)


def evaluate_rules(facts: IntakeFacts, config: IntakeConfig) -> tuple[RuleResult, ...]:
    """Return the six rule outcomes in the order shown in the PR checklist."""
    checks: tuple[Callable[[IntakeFacts, IntakeConfig], RuleResult], ...] = (
        _linked_issue,
        _author_assigned,
        _required_labels,
        _open_pr_limit,
        _cla,
        _ci_green,
    )
    return tuple(check(facts, config) for check in checks)


def _linked_issue(facts: IntakeFacts, config: IntakeConfig) -> RuleResult:
    """Check the configured linked issue requirement."""
    if not config.require_linked_issue:
        return RuleResult(RuleName.LINKED_ISSUE, True, "Linked issue is not required.")
    passed = LINKED_ISSUE_PATTERN.search(facts.body) is not None
    detail = "Linked issue found." if passed else "Add `Closes #n` to the pull request body."
    return RuleResult(RuleName.LINKED_ISSUE, passed, detail)


def _author_assigned(facts: IntakeFacts, config: IntakeConfig) -> RuleResult:
    """Check that the pull request author is assigned to its linked issue."""
    if not config.require_assignee:
        return RuleResult(RuleName.AUTHOR_ASSIGNED, True, "Issue assignment is not required.")
    passed = (
        facts.issue_assignee is not None
        and facts.issue_assignee.casefold() == facts.author.casefold()
    )
    detail = (
        "Author is assigned to the issue."
        if passed
        else "Assign the linked issue to the PR author."
    )
    return RuleResult(RuleName.AUTHOR_ASSIGNED, passed, detail)


def _required_labels(facts: IntakeFacts, config: IntakeConfig) -> RuleResult:
    """Check that every configured required label is present."""
    missing = sorted(set(config.required_labels).difference(facts.labels))
    passed = not missing
    detail = (
        "Required labels are present." if passed else f"Add required labels: {', '.join(missing)}."
    )
    return RuleResult(RuleName.REQUIRED_LABELS, passed, detail)


def _open_pr_limit(facts: IntakeFacts, config: IntakeConfig) -> RuleResult:
    """Check the author's open PR count against the configured cap."""
    passed = facts.open_prs_by_author <= config.max_open_prs_per_author
    detail = (
        "Open PR limit is satisfied."
        if passed
        else (
            f"Author has {facts.open_prs_by_author} open PRs; "
            f"limit is {config.max_open_prs_per_author}."
        )
    )
    return RuleResult(RuleName.OPEN_PR_LIMIT, passed, detail)


def _cla(facts: IntakeFacts, config: IntakeConfig) -> RuleResult:
    """Check CLA status when repository config requires it."""
    if not config.require_cla:
        return RuleResult(RuleName.CLA, True, "CLA check is not required.")
    passed = facts.cla_signed
    detail = "CLA is signed." if passed else "Contributor must sign the CLA."
    return RuleResult(RuleName.CLA, passed, detail)


def _ci_green(facts: IntakeFacts, config: IntakeConfig) -> RuleResult:
    """Check CI status when repository config requires green CI."""
    if not config.require_ci_green:
        return RuleResult(RuleName.CI_GREEN, True, "Green CI is not required.")
    passed = facts.ci_green
    detail = "CI is green." if passed else "CI must be green before triage continues."
    return RuleResult(RuleName.CI_GREEN, passed, detail)
