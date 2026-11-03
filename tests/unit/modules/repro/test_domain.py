"""Pure verdict rules for paired reproduction runs."""

import pytest

from heimdall.modules.repro.api import RunOutcome, Verdict, derive_verdict


@pytest.mark.parametrize(
    ("with_fix", "without_fix", "expected"),
    [
        (RunOutcome(True), RunOutcome(False), Verdict.FIX_CONFIRMED),
        (RunOutcome(False), RunOutcome(False), Verdict.FIX_FAILED),
        (RunOutcome(True), RunOutcome(True), Verdict.NOT_REPRODUCED),
    ],
)
def test_derive_verdict_compares_patched_and_base_results(
    with_fix: RunOutcome,
    without_fix: RunOutcome,
    expected: Verdict,
) -> None:
    """Classify whether the patch fixes a failing baseline or fails itself."""
    assert derive_verdict(with_fix, without_fix) is expected
