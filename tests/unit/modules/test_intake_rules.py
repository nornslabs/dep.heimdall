"""Tests for the six plain-code intake rules."""

from dataclasses import replace

import pytest

from heimdall.modules.intake.api import IntakeFacts, RuleName, evaluate_rules
from heimdall.modules.tenancy.domain import IntakeConfig

BASE_FACTS = IntakeFacts(
    author="contributor",
    body="Implements feature. Closes #42",
    labels=("bug", "good first issue"),
    open_prs_by_author=3,
    issue_assignee="Contributor",
    cla_signed=True,
    ci_green=True,
)
BASE_CONFIG = IntakeConfig(required_labels=("bug",), max_open_prs_per_author=5)


@pytest.mark.parametrize(
    ("rule", "facts", "config", "passed"),
    [
        (RuleName.LINKED_ISSUE, replace(BASE_FACTS, body="Closes #12"), BASE_CONFIG, True),
        (RuleName.LINKED_ISSUE, replace(BASE_FACTS, body="Fixes #12"), BASE_CONFIG, False),
        (RuleName.AUTHOR_ASSIGNED, BASE_FACTS, BASE_CONFIG, True),
        (
            RuleName.AUTHOR_ASSIGNED,
            replace(BASE_FACTS, issue_assignee="someone-else"),
            BASE_CONFIG,
            False,
        ),
        (RuleName.REQUIRED_LABELS, BASE_FACTS, BASE_CONFIG, True),
        (
            RuleName.REQUIRED_LABELS,
            replace(BASE_FACTS, labels=("bug",)),
            BASE_CONFIG.model_copy(update={"required_labels": ("bug", "security")}),
            False,
        ),
        (RuleName.OPEN_PR_LIMIT, BASE_FACTS, BASE_CONFIG, True),
        (
            RuleName.OPEN_PR_LIMIT,
            replace(BASE_FACTS, open_prs_by_author=6),
            BASE_CONFIG,
            False,
        ),
        (
            RuleName.CLA,
            replace(BASE_FACTS, cla_signed=True),
            BASE_CONFIG.model_copy(update={"require_cla": True}),
            True,
        ),
        (
            RuleName.CLA,
            replace(BASE_FACTS, cla_signed=False),
            BASE_CONFIG.model_copy(update={"require_cla": True}),
            False,
        ),
        (RuleName.CI_GREEN, BASE_FACTS, BASE_CONFIG, True),
        (
            RuleName.CI_GREEN,
            replace(BASE_FACTS, ci_green=False),
            BASE_CONFIG,
            False,
        ),
    ],
)
def test_each_rule_passes_and_fails_for_its_configured_facts(
    rule: RuleName,
    facts: IntakeFacts,
    config: IntakeConfig,
    passed: bool,
) -> None:
    """Each named rule reports its configured outcome from input facts."""
    results = evaluate_rules(facts, config)
    result = next(result for result in results if result.rule is rule)

    assert result.passed is passed
    assert result.detail


def test_evaluator_returns_six_rules_in_checklist_order() -> None:
    """The user-facing checklist has one stable row per rule."""
    results = evaluate_rules(BASE_FACTS, BASE_CONFIG)

    assert [result.rule for result in results] == list(RuleName)
