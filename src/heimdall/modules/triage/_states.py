"""Value types for the PR state machine: states, events, effects, facts and outcomes."""

from dataclasses import dataclass
from enum import StrEnum


class PrState(StrEnum):
    """Where a pull request is in triage; stored in ``prs.state``."""

    RECEIVED = "received"
    INTAKE_FAILED = "intake_failed"
    CHECKING = "checking"
    CARD_POSTED = "card_posted"
    DRAFT_PENDING = "draft_pending"
    APPROVED = "approved"
    CLOSED = "closed"


class EventKind(StrEnum):
    """Something that happened to a PR; GitHub webhooks and internal step results."""

    PR_OPENED = "pull_request.opened"
    PR_SYNCHRONIZE = "pull_request.synchronize"
    PR_EDITED = "pull_request.edited"
    PR_CLOSED = "pull_request.closed"
    INTAKE_DONE = "intake.done"
    REPRO_FINISHED = "repro.finished"
    REVIEW_FINISHED = "review.finished"
    MAINTAINER_COMMAND = "maintainer.command"
    MAINTAINER_RECHECK = "maintainer.recheck"
    FINDING_MECHANICAL = "finding.mechanical"
    REACTION_APPROVE = "reaction.approve"
    OUTBOX_SENT = "outbox.sent"


RESULT_EVENTS = frozenset(
    {EventKind.INTAKE_DONE, EventKind.REPRO_FINISHED, EventKind.REVIEW_FINISHED}
)
"""Events that report work done on one head commit; stale ones never move state."""


class Effect(StrEnum):
    """A side effect the service performs in the same transaction as the state change."""

    ENQUEUE_INTAKE = "enqueue_intake"
    OUTBOX_THREAD_AND_CHECKLIST = "outbox_thread_and_checklist"
    ROOM_READY_FOR_REPRO = "room_ready_for_repro"
    ROOM_READY_FOR_REVIEW = "room_ready_for_review"
    OUTBOX_FAILED_RULES = "outbox_failed_rules"
    ENQUEUE_INTAKE_REPLY = "enqueue_intake_reply"
    ROOM_READY_FOR_CARD = "room_ready_for_card"
    ROOM_EVENT_TO_HEIMDALL = "room_event_to_heimdall"
    APPROVE_BY_POLICY = "approve_by_policy"
    RECORD_APPROVAL = "record_approval"
    OUTBOX_GITHUB_COMMENT = "outbox_github_comment"
    AUDIT = "audit"
    MARK_OLDER_RUNS_STALE = "mark_older_runs_stale"
    CANCEL_JOBS = "cancel_jobs"
    ROOM_NOTE = "room_note"


class RejectReason(StrEnum):
    """Why an event did not move the PR."""

    NOT_ALLOWED_FROM_STATE = "not_allowed_from_state"
    STALE_HEAD = "stale_head"
    REPO_NOT_ENABLED = "repo_not_enabled"
    NOT_A_MAINTAINER = "not_a_maintainer"
    DRAFT_HASH_CHANGED = "draft_hash_changed"
    CATEGORY_NOT_AUTO_SUGGEST = "category_not_auto_suggest"


@dataclass(frozen=True)
class Facts:
    """What the guards need to know, gathered by the service before calling ``next_state``.

    Attributes:
        repo_enabled: Heimdall is enabled for the PR's repository.
        intake_passed: Every intake rule passed for the current head commit.
        repro_done: Huginn's repro finished for the current head commit.
        review_done: Loki's review finished for the current head commit.
        sender_is_maintainer: The person acting is on the repo's maintainer list.
        draft_hash_matches: The approved hash equals the draft's current ``body_hash``.
        category_auto_suggest: The finding's category is in the repo's ``auto_suggest`` list.
        result_is_for_current_head: A result event's ``head_sha`` is the PR's current head.
    """

    repo_enabled: bool = False
    intake_passed: bool = False
    repro_done: bool = False
    review_done: bool = False
    sender_is_maintainer: bool = False
    draft_hash_matches: bool = False
    category_auto_suggest: bool = False
    result_is_for_current_head: bool = True


@dataclass(frozen=True)
class Transition:
    """The PR moves to ``to``; the service performs ``effects`` in the same transaction."""

    to: PrState
    effects: tuple[Effect, ...]


@dataclass(frozen=True)
class Rejected:
    """The event leaves the PR where it is; the service records why and stops."""

    reason: RejectReason
