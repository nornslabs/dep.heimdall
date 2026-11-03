"""Pure finding validation tests."""

from heimdall.modules.review.api import FindingInput, validate_findings
from heimdall.modules.tenancy.api import ReviewConfig


def test_validate_findings_rejects_invalid_citations_and_skipped_paths() -> None:
    """Keep only findings with a changed file, positive line and usable text."""
    findings = (
        FindingInput("src/app.py", 7, "Handle empty input", "warning"),
        FindingInput("", 3, "Missing file", "warning"),
        FindingInput("src/app.py", 0, "Invalid line", "warning"),
        FindingInput("src/other.py", 2, "Not changed", "warning"),
        FindingInput("docs/readme.md", 4, "Skipped", "warning"),
        FindingInput("src/app.py", 9, "", "warning"),
    )

    batch = validate_findings(findings, frozenset({"src/app.py", "docs/readme.md"}), ReviewConfig())

    assert batch.accepted == (findings[0],)
    assert [item.reason for item in batch.rejected] == [
        "missing_file",
        "invalid_line",
        "file_not_changed",
        "path_skipped",
        "missing_text",
    ]


def test_validate_findings_enforces_the_configured_cap() -> None:
    """Reject findings beyond the repo's configured limit."""
    findings = tuple(
        FindingInput("src/app.py", line, f"Finding {line}", "warning") for line in range(1, 4)
    )

    batch = validate_findings(
        findings,
        frozenset({"src/app.py"}),
        ReviewConfig(max_findings=2),
    )

    assert batch.accepted == findings[:2]
    assert batch.rejected[0].reason == "finding_limit_reached"
