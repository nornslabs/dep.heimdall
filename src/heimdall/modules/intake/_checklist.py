"""Render intake rule outcomes as a Switch room checklist."""

from heimdall.modules.intake.domain import RuleResult


def render_checklist(number: int, results: tuple[RuleResult, ...]) -> str:
    """Format rule results for a maintainer-readable PR thread.

    Args:
        number: Pull request number.
        results: Outcomes in the stable intake rule order.

    Returns:
        Markdown checklist suitable for a Switch post.
    """
    lines = [f"Intake checklist for pull request #{number}"]
    for result in results:
        marker = "x" if result.passed else " "
        lines.append(f"- [{marker}] {result.rule.value}: {result.detail}")
    if all(result.passed for result in results):
        lines.append("Ready for reproduction and review.")
    else:
        lines.append("Resolve the failed checks and push an updated commit to retry intake.")
    return "\n".join(lines)
