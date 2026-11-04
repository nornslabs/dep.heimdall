"""Triage counts a check as done only when it finished on the PR's current head."""

from heimdall.modules.triage.domain import CheckHeads, Facts, with_checks_done

HEAD = "head-2"
OLD_HEAD = "head-1"


def test_earlier_repro_on_this_head_counts_for_the_review_result() -> None:
    """Loki does not know Huginn finished; the stored head says so."""
    facts = with_checks_done(Facts(review_done=True), HEAD, CheckHeads(repro=HEAD, review=None))
    assert (facts.repro_done, facts.review_done) == (True, True)


def test_earlier_review_on_this_head_counts_for_the_repro_result() -> None:
    """Huginn does not know Loki finished; the stored head says so."""
    facts = with_checks_done(Facts(repro_done=True), HEAD, CheckHeads(repro=None, review=HEAD))
    assert (facts.repro_done, facts.review_done) == (True, True)


def test_check_finished_on_an_older_head_is_still_pending() -> None:
    """A push since the repro finished means it has to run again."""
    heads = CheckHeads(repro=OLD_HEAD, review=None)
    facts = with_checks_done(Facts(review_done=True), HEAD, heads)
    assert (facts.repro_done, facts.review_done) == (False, True)


def test_reported_facts_are_kept_when_nothing_is_stored() -> None:
    """The caller's own result is never dropped."""
    facts = with_checks_done(Facts(repro_done=True), HEAD, CheckHeads(repro=None, review=None))
    assert (facts.repro_done, facts.review_done) == (True, False)
