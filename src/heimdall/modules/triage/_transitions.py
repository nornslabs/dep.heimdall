"""The PR state machine table from the LLD, one rule per row, in LLD order."""

from collections.abc import Iterable
from dataclasses import dataclass

from heimdall.modules.triage import _guards as g
from heimdall.modules.triage._guards import Guard
from heimdall.modules.triage._states import Effect, EventKind, PrState

OPEN_STATES = frozenset(state for state in PrState if state is not PrState.CLOSED)


@dataclass(frozen=True)
class Rule:
    """One row: from any of ``from_states`` on ``event``, if every guard holds, go to ``to``.

    ``None`` in ``from_states`` means a PR Heimdall has not seen yet.
    """

    from_states: frozenset[PrState | None]
    event: EventKind
    guards: tuple[Guard, ...]
    to: PrState
    effects: tuple[Effect, ...]


def _rule(
    from_states: Iterable[PrState | None],
    event: EventKind,
    guards: tuple[Guard, ...],
    to: PrState,
    effects: tuple[Effect, ...],
) -> Rule:
    return Rule(frozenset(from_states), event, guards, to, effects)


_CHECKING: frozenset[PrState | None] = frozenset({PrState.CHECKING})
_BOTH_DONE = (g.BOTH_CHECKS_DONE,)
_ONE_PENDING = (g.ONE_CHECK_PENDING,)
_TO_CARD = (Effect.ROOM_READY_FOR_CARD,)

TRANSITIONS: tuple[Rule, ...] = (
    _rule({None}, EventKind.PR_OPENED, (g.REPO_ENABLED,), PrState.RECEIVED,
          (Effect.ENQUEUE_INTAKE,)),
    _rule({PrState.RECEIVED}, EventKind.INTAKE_DONE, (g.INTAKE_PASSED,), PrState.CHECKING,
          (Effect.OUTBOX_THREAD_AND_CHECKLIST, Effect.ROOM_READY_FOR_REPRO,
           Effect.ROOM_READY_FOR_REVIEW)),
    _rule({PrState.RECEIVED}, EventKind.INTAKE_DONE, (g.INTAKE_FAILED,), PrState.INTAKE_FAILED,
          (Effect.OUTBOX_FAILED_RULES, Effect.ENQUEUE_INTAKE_REPLY)),
    _rule(_CHECKING, EventKind.REPRO_FINISHED, _BOTH_DONE, PrState.CARD_POSTED, _TO_CARD),
    _rule(_CHECKING, EventKind.REVIEW_FINISHED, _BOTH_DONE, PrState.CARD_POSTED, _TO_CARD),
    _rule(_CHECKING, EventKind.REPRO_FINISHED, _ONE_PENDING, PrState.CHECKING, ()),
    _rule(_CHECKING, EventKind.REVIEW_FINISHED, _ONE_PENDING, PrState.CHECKING, ()),
    _rule({PrState.CARD_POSTED}, EventKind.MAINTAINER_COMMAND, (g.SENDER_IS_MAINTAINER,),
          PrState.DRAFT_PENDING, (Effect.ROOM_EVENT_TO_HEIMDALL,)),
    _rule({PrState.CARD_POSTED}, EventKind.FINDING_MECHANICAL, (g.CATEGORY_AUTO_SUGGEST,),
          PrState.APPROVED, (Effect.APPROVE_BY_POLICY, Effect.OUTBOX_GITHUB_COMMENT)),
    _rule({PrState.DRAFT_PENDING}, EventKind.REACTION_APPROVE,
          (g.SENDER_IS_MAINTAINER, g.DRAFT_HASH_MATCHES), PrState.APPROVED,
          (Effect.RECORD_APPROVAL, Effect.OUTBOX_GITHUB_COMMENT)),
    _rule({PrState.APPROVED}, EventKind.OUTBOX_SENT, (), PrState.CARD_POSTED, (Effect.AUDIT,)),
    _rule(OPEN_STATES, EventKind.PR_SYNCHRONIZE, (), PrState.RECEIVED,
          (Effect.ENQUEUE_INTAKE, Effect.MARK_OLDER_RUNS_STALE)),
    _rule({PrState.INTAKE_FAILED}, EventKind.PR_EDITED, (), PrState.RECEIVED,
          (Effect.ENQUEUE_INTAKE, Effect.MARK_OLDER_RUNS_STALE)),
    _rule(OPEN_STATES, EventKind.PR_CLOSED, (), PrState.CLOSED,
          (Effect.CANCEL_JOBS, Effect.ROOM_NOTE)),
)  # fmt: skip
