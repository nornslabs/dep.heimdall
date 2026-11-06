"""next_state follows the LLD state machine table row by row."""

import itertools

import pytest

from heimdall.modules.triage.domain import (
    TRANSITIONS,
    Effect,
    EventKind,
    Facts,
    PrState,
    Rejected,
    RejectReason,
    Transition,
    next_state,
)

OPEN_STATES = [state for state in PrState if state is not PrState.CLOSED]
RESULT_EVENTS = [EventKind.INTAKE_DONE, EventKind.REPRO_FINISHED, EventKind.REVIEW_FINISHED]
CHECK_EVENTS = [EventKind.REPRO_FINISHED, EventKind.REVIEW_FINISHED]


# --- one test per LLD row -------------------------------------------------------------


def test_opened_on_enabled_repo_starts_at_received_and_queues_intake() -> None:
    result = next_state(None, EventKind.PR_OPENED, Facts(repo_enabled=True))

    assert result == Transition(PrState.RECEIVED, (Effect.ENQUEUE_INTAKE,))


def test_intake_pass_moves_received_to_checking_and_wakes_both_agents() -> None:
    result = next_state(PrState.RECEIVED, EventKind.INTAKE_DONE, Facts(intake_passed=True))

    assert result == Transition(
        PrState.CHECKING,
        (
            Effect.OUTBOX_THREAD_AND_CHECKLIST,
            Effect.ROOM_READY_FOR_REPRO,
            Effect.ROOM_READY_FOR_REVIEW,
        ),
    )


def test_intake_fail_moves_received_to_intake_failed_and_reports_rules() -> None:
    result = next_state(PrState.RECEIVED, EventKind.INTAKE_DONE, Facts(intake_passed=False))

    assert result == Transition(
        PrState.INTAKE_FAILED, (Effect.OUTBOX_FAILED_RULES, Effect.ENQUEUE_INTAKE_REPLY)
    )


def test_editing_a_pr_that_failed_intake_reruns_intake() -> None:
    result = next_state(PrState.INTAKE_FAILED, EventKind.PR_EDITED, Facts())

    assert result == Transition(
        PrState.RECEIVED, (Effect.ENQUEUE_INTAKE, Effect.MARK_OLDER_RUNS_STALE)
    )


@pytest.mark.parametrize("event", CHECK_EVENTS)
def test_both_checks_done_posts_the_card(event: EventKind) -> None:
    result = next_state(PrState.CHECKING, event, Facts(repro_done=True, review_done=True))

    assert result == Transition(PrState.CARD_POSTED, (Effect.ROOM_READY_FOR_CARD,))


@pytest.mark.parametrize(
    ("event", "facts"),
    [
        (EventKind.REPRO_FINISHED, Facts(repro_done=True)),
        (EventKind.REVIEW_FINISHED, Facts(review_done=True)),
    ],
)
def test_one_check_done_keeps_checking_with_no_effects(event: EventKind, facts: Facts) -> None:
    assert next_state(PrState.CHECKING, event, facts) == Transition(PrState.CHECKING, ())


def test_maintainer_command_on_card_moves_to_draft_pending() -> None:
    result = next_state(
        PrState.CARD_POSTED, EventKind.MAINTAINER_COMMAND, Facts(sender_is_maintainer=True)
    )

    assert result == Transition(PrState.DRAFT_PENDING, (Effect.ROOM_EVENT_TO_HEIMDALL,))


def test_auto_suggest_mechanical_finding_is_approved_by_policy() -> None:
    result = next_state(
        PrState.CARD_POSTED, EventKind.FINDING_MECHANICAL, Facts(category_auto_suggest=True)
    )

    assert result == Transition(
        PrState.APPROVED, (Effect.APPROVE_BY_POLICY, Effect.OUTBOX_GITHUB_COMMENT)
    )


def test_maintainer_approval_with_matching_hash_is_approved() -> None:
    facts = Facts(sender_is_maintainer=True, draft_hash_matches=True)

    result = next_state(PrState.DRAFT_PENDING, EventKind.REACTION_APPROVE, facts)

    assert result == Transition(
        PrState.APPROVED, (Effect.RECORD_APPROVAL, Effect.OUTBOX_GITHUB_COMMENT)
    )


def test_sent_comment_returns_pr_to_card_posted() -> None:
    result = next_state(PrState.APPROVED, EventKind.OUTBOX_SENT, Facts())

    assert result == Transition(PrState.CARD_POSTED, (Effect.AUDIT,))


@pytest.mark.parametrize("state", OPEN_STATES)
def test_new_push_returns_any_open_pr_to_received(state: PrState) -> None:
    result = next_state(state, EventKind.PR_SYNCHRONIZE, Facts())

    assert result == Transition(
        PrState.RECEIVED, (Effect.ENQUEUE_INTAKE, Effect.MARK_OLDER_RUNS_STALE)
    )


@pytest.mark.parametrize("state", OPEN_STATES)
def test_closing_any_open_pr_moves_it_to_closed(state: PrState) -> None:
    result = next_state(state, EventKind.PR_CLOSED, Facts())

    assert result == Transition(PrState.CLOSED, (Effect.CANCEL_JOBS, Effect.ROOM_NOTE))


# --- guard failures -------------------------------------------------------------------


def test_opened_on_disabled_repo_is_rejected() -> None:
    result = next_state(None, EventKind.PR_OPENED, Facts(repo_enabled=False))

    assert result == Rejected(RejectReason.REPO_NOT_ENABLED)


def test_command_from_non_maintainer_is_rejected() -> None:
    result = next_state(PrState.CARD_POSTED, EventKind.MAINTAINER_COMMAND, Facts())

    assert result == Rejected(RejectReason.NOT_A_MAINTAINER)


def test_approval_from_non_maintainer_is_rejected() -> None:
    facts = Facts(sender_is_maintainer=False, draft_hash_matches=True)

    result = next_state(PrState.DRAFT_PENDING, EventKind.REACTION_APPROVE, facts)

    assert result == Rejected(RejectReason.NOT_A_MAINTAINER)


def test_approval_with_changed_hash_is_rejected() -> None:
    facts = Facts(sender_is_maintainer=True, draft_hash_matches=False)

    result = next_state(PrState.DRAFT_PENDING, EventKind.REACTION_APPROVE, facts)

    assert result == Rejected(RejectReason.DRAFT_HASH_CHANGED)


def test_mechanical_finding_outside_auto_suggest_is_rejected() -> None:
    result = next_state(PrState.CARD_POSTED, EventKind.FINDING_MECHANICAL, Facts())

    assert result == Rejected(RejectReason.CATEGORY_NOT_AUTO_SUGGEST)


# --- stale results and invalid transitions --------------------------------------------


@pytest.mark.parametrize("event", RESULT_EVENTS)
def test_result_for_an_old_head_never_moves_state(event: EventKind) -> None:
    facts = Facts(
        intake_passed=True, repro_done=True, review_done=True, result_is_for_current_head=False
    )
    state = PrState.RECEIVED if event is EventKind.INTAKE_DONE else PrState.CHECKING

    assert next_state(state, event, facts) == Rejected(RejectReason.STALE_HEAD)


def _pairs_in_table() -> set[tuple[PrState | None, EventKind]]:
    pairs: set[tuple[PrState | None, EventKind]] = set()
    for rule in TRANSITIONS:
        for state in rule.from_states:
            pairs.add((state, rule.event))
    return pairs


def _pairs_not_in_table() -> list[tuple[PrState | None, EventKind]]:
    every_pair = itertools.product([None, *PrState], EventKind)
    in_table = _pairs_in_table()
    return [pair for pair in every_pair if pair not in in_table]


@pytest.mark.parametrize(("state", "event"), _pairs_not_in_table())
def test_event_not_allowed_from_state_is_rejected(state: PrState | None, event: EventKind) -> None:
    every_fact_true = Facts(
        repo_enabled=True,
        intake_passed=True,
        repro_done=True,
        review_done=True,
        sender_is_maintainer=True,
        draft_hash_matches=True,
        category_auto_suggest=True,
    )

    assert next_state(state, event, every_fact_true) == Rejected(
        RejectReason.NOT_ALLOWED_FROM_STATE
    )


@pytest.mark.parametrize("event", list(EventKind))
def test_closed_is_final(event: EventKind) -> None:
    assert isinstance(next_state(PrState.CLOSED, event, Facts(repo_enabled=True)), Rejected)


def test_command_on_pending_draft_is_not_in_the_lld_table() -> None:
    result = next_state(
        PrState.DRAFT_PENDING, EventKind.MAINTAINER_COMMAND, Facts(sender_is_maintainer=True)
    )

    assert result == Rejected(RejectReason.NOT_ALLOWED_FROM_STATE)


# --- structure ------------------------------------------------------------------------


@pytest.mark.parametrize("state", OPEN_STATES)
def test_every_open_state_can_reach_closed(state: PrState) -> None:
    assert (state, EventKind.PR_CLOSED) in _pairs_in_table()


def test_every_state_is_reachable_from_opened() -> None:
    reachable = {PrState.RECEIVED}
    for rule in TRANSITIONS:
        reachable.add(rule.to)

    assert reachable == set(PrState)


# --- maintainer recheck ---------------------------------------------------------------

RECHECKABLE_STATES = [PrState.INTAKE_FAILED, PrState.CHECKING, PrState.CARD_POSTED]


@pytest.mark.parametrize("state", RECHECKABLE_STATES)
def test_maintainer_recheck_reruns_intake_and_stales_older_runs(state: PrState) -> None:
    result = next_state(state, EventKind.MAINTAINER_RECHECK, Facts(sender_is_maintainer=True))

    assert result == Transition(
        PrState.RECEIVED, (Effect.ENQUEUE_INTAKE, Effect.MARK_OLDER_RUNS_STALE)
    )


@pytest.mark.parametrize("state", RECHECKABLE_STATES)
def test_recheck_from_a_non_maintainer_is_rejected(state: PrState) -> None:
    result = next_state(state, EventKind.MAINTAINER_RECHECK, Facts(sender_is_maintainer=False))

    assert result == Rejected(RejectReason.NOT_A_MAINTAINER)


@pytest.mark.parametrize(
    "state", [None, PrState.RECEIVED, PrState.DRAFT_PENDING, PrState.APPROVED, PrState.CLOSED]
)
def test_recheck_is_refused_where_it_would_disturb_work_in_flight(
    state: PrState | None,
) -> None:
    result = next_state(state, EventKind.MAINTAINER_RECHECK, Facts(sender_is_maintainer=True))

    assert result == Rejected(RejectReason.NOT_ALLOWED_FROM_STATE)
