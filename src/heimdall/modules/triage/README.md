# triage

The PR triage state machine. Only this module changes `prs.state`, and only through
`domain.next_state` (AGENTS.md rule 8).

Public functions: `receive_webhook`, `handle_webhook_delivery`, `handle_event`, `on_step_done`,
`on_step_done_in_transaction`, `get_pr_snapshot`, and `get_switch_thread_id` /
`save_switch_thread_id` (the Switch thread every agent replies into; the first saved wins). `domain.py` exposes the pure
`next_state` transition function, `with_checks_done`, and its event, fact, state, and
outcome types.

| File | Role |
|---|---|
| `api.py` | Public interface (`handle_event`, `on_step_done`, `receive_webhook`) |
| `service.py` | Transactional state transitions and queue effects |
| `_webhooks.py` | Persist verified deliveries and enqueue their handlers |
| `_checks.py` | Store the head each agent check last finished on |
| `domain.py` | `next_state(state, event, facts) -> Transition \| Rejected`, plus the types below. Pure, no I/O |
| `_states.py` | `PrState`, `EventKind`, `Effect`, `Facts`, `Transition`, `Rejected`, `RejectReason` |
| `_guards.py` | One named condition per guard, each with the reason it reports |
| `_transitions.py` | `TRANSITIONS`: the LLD table, one rule per row |
| `ports.py` | Adapter interfaces |
| `models.py` | `prs` (with `repro_done_head`, `review_done_head`), `events` |

## Transitions (from the LLD)

| From | Event | Guard | To | Effects |
|---|---|---|---|---|
| none | `pull_request.opened` | repo enabled | `received` | enqueue intake |
| `received` | `intake.done` | all rules pass | `checking` | outbox checklist as the PR's thread root, posted by Heimdall when the repo has an agent room (else the bot); enqueue Huginn with validated repro command; enqueue `loki.run_review` |
| `received` | `intake.done` | any rule fails | `intake_failed` | outbox failed rules |
| `checking` | `repro.finished` / `review.finished` | both done | `card_posted` | enqueue `heimdall.build_card` |
| `checking` | either finished | only one done | `checking` | none |
| `card_posted` | `maintainer.command` | sender is maintainer | `draft_pending` | room event to Heimdall |
| `card_posted` | `finding.mechanical` | category in `auto_suggest` | `approved` | approve by policy, outbox GitHub comment |
| `draft_pending` | `reaction.approve` | maintainer, then hash matches | `approved` | record approval, outbox GitHub comment |
| `approved` | `outbox.sent` | none | `card_posted` | audit |
| any open | `pull_request.synchronize` | none | `received` | enqueue intake, mark older runs stale |
| any open | `pull_request.closed` | none | `closed` | cancel jobs, room note |

## Rejections

`next_state` returns `Rejected(reason)` instead of raising, because late and duplicate events
are normal:

- **`stale_head`:** a result event (`intake.done`, `repro.finished`, `review.finished`) for an
  old head commit. Results like this are stored but never move state.
- **A failed guard's reason:** `repo_not_enabled`, `not_a_maintainer`, `draft_hash_changed`,
  `category_not_auto_suggest`.
- **`not_allowed_from_state`:** the table has no row for this state and event.

Choices the LLD leaves open:
- `closed` takes no further events, so a redelivered close doesn't post a second room note.
- Events for a PR Heimdall hasn't seen yet are rejected, except `pull_request.opened`.
- A `maintainer.command` while a draft is pending is rejected, because the table allows it
  only from `card_posted`.

## Both checks done

Huginn and Loki each report only their own check. `on_step_done_in_transaction` locks the
PR row, adds any check already finished on the same head (`with_checks_done`), and stores
the finishing check's head in `repro_done_head` or `review_done_head`. A check counts only
while its stored head equals `prs.head_sha`, so a push makes both pending again.
