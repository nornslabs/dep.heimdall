"""Pure validation for Loki's file and line findings."""

from dataclasses import dataclass
from fnmatch import fnmatchcase
from uuid import UUID

from heimdall.modules.tenancy.api import ReviewConfig


@dataclass(frozen=True)
class FindingInput:
    """One untrusted finding proposed by Loki.

    Attributes:
        file: Repository relative path cited by the finding.
        line: One based line number in the changed file.
        text: Finding explanation.
        severity: Reviewer supplied severity label.
        mechanical_category: Optional auto suggestion category.
    """

    file: str
    line: int
    text: str
    severity: str
    mechanical_category: str | None = None


@dataclass(frozen=True)
class RejectedFinding:
    """Location and reason for a rejected finding, without repeating its text."""

    index: int
    file: str
    line: int
    reason: str


@dataclass(frozen=True)
class FindingRef:
    """Saved finding values safe to pass across the review module border."""

    id: UUID
    file: str
    line: int
    text: str
    severity: str
    mechanical_category: str | None


@dataclass(frozen=True)
class FindingBatch:
    """Accepted findings and validation failures in input order."""

    accepted: tuple[FindingInput, ...]
    rejected: tuple[RejectedFinding, ...]


def validate_findings(
    findings: tuple[FindingInput, ...],
    changed_paths: frozenset[str],
    config: ReviewConfig,
) -> FindingBatch:
    """Keep only well-formed findings on changed, non-skipped files.

    Args:
        findings: Untrusted proposals from Loki.
        changed_paths: Repository-relative paths in this PR's diff.
        config: Effective review limits and skip patterns.

    Returns:
        Accepted findings up to the configured cap and reasons for every rejection.
    """
    accepted: list[FindingInput] = []
    rejected: list[RejectedFinding] = []
    for index, finding in enumerate(findings):
        reason = _rejection_reason(finding, changed_paths, config)
        if reason is None and len(accepted) >= config.max_findings:
            reason = "finding_limit_reached"
        if reason is None:
            accepted.append(finding)
        else:
            rejected.append(RejectedFinding(index, finding.file, finding.line, reason))
    return FindingBatch(tuple(accepted), tuple(rejected))


def _rejection_reason(
    finding: FindingInput,
    changed_paths: frozenset[str],
    config: ReviewConfig,
) -> str | None:
    """Return the first rule that makes a finding unsafe to save."""
    if not finding.file.strip():
        return "missing_file"
    if finding.line < 1:
        return "invalid_line"
    if not finding.text.strip():
        return "missing_text"
    if finding.file not in changed_paths:
        return "file_not_changed"
    if any(fnmatchcase(finding.file, pattern) for pattern in config.skip_paths):
        return "path_skipped"
    return None


__all__ = [
    "FindingBatch",
    "FindingInput",
    "FindingRef",
    "RejectedFinding",
    "validate_findings",
]
