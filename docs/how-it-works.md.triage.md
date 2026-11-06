# Triage state machine and transition table

**Audience:** contributors, operators, reviewers.
**Purpose:** the exact rules that move a PR from webhook to approved comment, so the behaviour is
reviewable without reading `modules/triage/_transitions.py`.

`modules/triage/domain.py` exposes the pure `next_state(state, event, facts)` function. It returns
a `Transition` (new state plus effects) or a `Rejected(reason)`. Late and duplicate events are
normal, so rejections are returned, not raised.

## Transition table (from the LLD)

| From | Event | Guard | To | Effects |
|---|---|---|---|---|
| none | `pull_request.opened` | repo enabled | `received` | enqueue intake |
| `received` | `intake.done` | all rules pass | `checking` | outbox heading `New PR #<no> <title>` as the PR's thread root (only while the PR has no thread) and the checklist as its reply, posted by Heimdall when the repo has an agent room (else the bot posts the flat checklist); enqueue Huginn with validated repro command; enqueue `loki.run_review` |
| `received` | `intake.done` | any rule fails | `intake_failed` | outbox failed rules; enqueue `heimdall.draft_intake_reply` |
| `intake_failed` | `pull_request.edited` (also label/assignee changes) | none | `received` | enqueue intake, mark older runs stale |
| `intake_failed`, `checking`, `card_posted` | `maintainer.recheck` | sender is maintainer | `received` | enqueue intake, mark older runs stale |
| `checking` | `repro.finished` / `review.finished` | both done | `card_posted` | enqueue `heimdall.build_card` |
| `checking` | either finished | only one done | `checking` | none |
| `card_posted` | `maintainer.command` | sender is maintainer | `draft_pending` | room event to Heimdall |
| `card_posted` | `finding.mechanical` | category in `auto_suggest` | `approved` | approve by policy, outbox GitHub comment |
| `draft_pending` | `reaction.approve` | maintainer, then hash matches | `approved` | record approval, outbox GitHub comment |
| `approved` | `outbox.sent` | none | `card_posted` | audit |
| any open | `pull_request.synchronize` | none | `received` | enqueue intake, mark older runs stale |
| any open | `pull_request.closed` | none | `closed` | cancel jobs, room note |

## Rejections

`next_state` returns `Rejected(reason)` instead of raising:

- **`stale_head`**: a result event (`intake.done`, `repro.finished`, `review.finished`) for an old
  head commit. Results like this are stored but never move state.
- **A failed guard's reason**: `repo_not_enabled`, `not_a_maintainer`, `draft_hash_changed`,
  `category_not_auto_suggest`.
- **`not_allowed_from_state`**: the table has no row for this state and event.

## Choices the LLD leaves open

- `closed` takes no further events, so a redelivered close doesn't post a second room note.
- Events for a PR Heimdall hasn't seen yet are rejected, except `pull_request.opened`.
- A `maintainer.command` while a draft is pending is rejected, because the table allows it only
  from `card_posted`.

## Both checks done

`on_step_done_in_transaction` locks the PR row, adds any check already finished on the same head
(`with_checks_done`), and stores the finishing check's head in `repro_done_head` or
`review_done_head`. A check counts only while its stored head equals `prs.head_sha`, so a push
makes both pending again.

## Draft and approval binding

A draft's `body_hash` (SHA-256 of its exact body) is what an approval must match. Gjallarhorn
checks that an approval exists whose `draft_hash` matches the text being posted, immediately before
sending. Reproduction workflow dispatches use a separate tenant-scoped policy approval row whose hash
covers the repository, workflow, branch ref, and all inputs.