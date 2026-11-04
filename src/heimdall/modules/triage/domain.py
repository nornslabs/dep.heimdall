"""The PR triage state machine: a pure function over the LLD transition table (no I/O)."""

import uuid
from dataclasses import dataclass, replace

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
from heimdall.shared.ids import RepoId

__all__ = [
    "TRANSITIONS",
    "CheckHeads",
    "Effect",
    "EventKind",
    "Facts",
    "PrEvent",
    "PrSnapshot",
    "PrState",
    "RejectReason",
    "Rejected",
    "Rule",
    "Transition",
    "next_state",
    "with_checks_done",
]


@dataclass(frozen=True)
class PrEvent:
    """Validated event facts needed to advance one pull request.

    Attributes:
        event_id: Stable delivery or step idempotency key.
        repo_id: GitHub repository that owns the pull request.
        number: Pull request number in that repository.
        author: Pull request author login.
        head_sha: Head commit associated with this event.
        base_sha: Base commit associated with this event.
        kind: State machine event to apply.
        facts: Guard facts already gathered from trusted adapters and stored results.
        linked_issue: Optional issue linked from intake.
        room_id: Optional Mattermost channel the bot posts the checklist to.
        agent_room_id: Optional Switch room UUID. When set, the checklist is posted by Heimdall
            as the root of the PR's thread instead of by the bot.
        room_message: Optional rendered checklist or failure details.
        thread_heading: Optional rendered heading; when set, Heimdall posts it as the thread
            root and the checklist replies under it.
        repo_full_name: GitHub owner and repository name for queued intake.
    """

    event_id: str
    repo_id: RepoId
    number: int
    author: str
    head_sha: str
    base_sha: str
    kind: EventKind
    facts: Facts
    linked_issue: int | None = None
    room_id: str | None = None
    agent_room_id: str | None = None
    room_message: str | None = None
    thread_heading: str | None = None
    repro_test_cmd: str | None = None
    repo_full_name: str = ""
    base_ref: str | None = None


@dataclass(frozen=True)
class PrSnapshot:
    """Read-only PR state exposed across the triage module boundary."""

    id: uuid.UUID
    repo_id: RepoId
    number: int
    author: str
    head_sha: str
    base_sha: str
    linked_issue: int | None
    state: PrState
    state_version: int
    base_ref: str | None = None


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


@dataclass(frozen=True)
class CheckHeads:
    """The head each agent check last finished on, as triage stored it."""

    repro: str | None
    review: str | None


def with_checks_done(facts: Facts, head_sha: str, done: CheckHeads) -> Facts:
    """Add the checks that already finished on ``head_sha`` to a step's reported facts.

    Huginn and Loki each report only their own result, so triage fills in the other one.
    A check that finished on an older head stays pending: the push needs it to run again.

    Args:
        facts: What the finishing step reported.
        head_sha: Commit the finishing step evaluated.
        done: Heads stored for each check on the PR row.

    Returns:
        ``facts`` with ``repro_done`` and ``review_done`` set for checks done on this head.
    """
    return replace(
        facts,
        repro_done=facts.repro_done or done.repro == head_sha,
        review_done=facts.review_done or done.review == head_sha,
    )


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
