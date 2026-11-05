"""The room message for a finished Huginn run, rendered from its validated verdict."""

from agents.huginn.schemas import HuginnVerdict, ModeResult

VERDICT_LABELS: dict[str, str] = {
    "reproduced": "the paired tests reproduce the issue.",
    "not_covered": "the paired tests do not exercise the change.",
    "setup_failed": "the tests could not run the change cleanly.",
    "inconclusive": "the runs disagree, so there is no verdict.",
}


def render_verdict(verdict: HuginnVerdict) -> str:
    """Render the room message from a validated verdict, never from the model's reply.

    Args:
        verdict: The submission that passed schema validation and the run cross-check.

    Returns:
        The verdict, the command, both counter sets, the one failing assertion the model
        named (if any) and its summary. Every line comes from a validated field.
    """
    lines = [
        f"Huginn: {VERDICT_LABELS[verdict.verdict]}",
        f"Test command: {_one_line(verdict.test_cmd)}",
        f"Runs: {len(verdict.repro_ids)}.",
        f"With fix: {_counts(verdict.with_fix)}; without fix: {_counts(verdict.without_fix)}.",
    ]
    if verdict.failing_assertion:
        lines.append(f"Failing assertion: {_one_line(verdict.failing_assertion)}")
    summary = _one_line(verdict.summary)
    if summary:
        lines.append(summary)
    return "\n".join(lines)


def _counts(mode: ModeResult) -> str:
    """Say how the named runs ended in one mode."""
    return f"{mode.passed} passed, {mode.failed} failed, {mode.errors} errors"


def _one_line(text: str) -> str:
    """Collapse prose onto one line, so model text cannot add room structure of its own."""
    return " ".join(text.split())
