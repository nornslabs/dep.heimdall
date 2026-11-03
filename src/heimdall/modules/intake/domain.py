"""Pure intake facts and results shared across module boundaries."""

from dataclasses import dataclass
from enum import StrEnum


class RuleName(StrEnum):
    """The six plain-code checks applied to a pull request."""

    LINKED_ISSUE = "linked_issue"
    AUTHOR_ASSIGNED = "author_assigned_to_issue"
    REQUIRED_LABELS = "required_labels"
    OPEN_PR_LIMIT = "open_pr_limit"
    CLA = "cla"
    CI_GREEN = "ci_green"


@dataclass(frozen=True)
class IntakeFacts:
    """Read-only GitHub facts needed to evaluate a PR against repository rules."""

    author: str
    body: str
    labels: tuple[str, ...]
    open_prs_by_author: int
    issue_assignee: str | None
    cla_signed: bool
    ci_green: bool


@dataclass(frozen=True)
class RuleResult:
    """One intake rule's stable name, outcome and maintainer-facing detail."""

    rule: RuleName
    passed: bool
    detail: str
