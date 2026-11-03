"""The PR triage state machine: a pure function over the LLD transition table (no I/O)."""

from heimdall.modules.triage._guards import Guard
from heimdall.modules.triage._states import (
    RESULT_EVENTS,
    Effect,
    EventKind,
    Facts,
    PrState,
    Rejected,
    RejectReason,
    Transition,
)
from heimdall.modules.triage._transitions import TRANSITIONS, Rule

__all__ = [
    "TRANSITIONS",
    "Effect",
    "EventKind",
    "Facts",
    "PrState",
    "RejectReason",
    "Rejected",
    "Rule",
    "Transition",
    "next_state",
]


def next_state(state: PrState | None, event: EventKind, facts: Facts) -> Transition | Rejected:
    """Decide where an event takes a PR, and which side effects go with it.

    Late and duplicate events are normal (redelivered webhooks, retried jobs), so a refusal
    is returned as ``Rejected`` rather than raised; the caller records it and stops.

    Args:
        state: The PR's current state, or ``None`` if Heimdall has not seen the PR yet.
        event: What happened.
        facts: What the guards need, gathered by the caller.

    Returns:
        ``Transition`` with the new state and effects, or ``Rejected`` with the reason.
    """
    if event in RESULT_EVENTS and not facts.result_is_for_current_head:
        return Rejected(RejectReason.STALE_HEAD)
    first_failure: RejectReason | None = None
    for rule in _rules_for(state, event):
        failed = _first_failed_guard(rule.guards, facts)
        if failed is None:
            return Transition(rule.to, rule.effects)
        first_failure = first_failure or failed.reason
    return Rejected(first_failure or RejectReason.NOT_ALLOWED_FROM_STATE)


def _rules_for(state: PrState | None, event: EventKind) -> list[Rule]:
    rules: list[Rule] = []
    for rule in TRANSITIONS:
        if rule.event is event and state in rule.from_states:
            rules.append(rule)
    return rules


def _first_failed_guard(guards: tuple[Guard, ...], facts: Facts) -> Guard | None:
    for guard in guards:
        if not guard.check(facts):
            return guard
    return None
