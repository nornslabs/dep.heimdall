# How Heimdall works

**Audience:** everyone (contributors, operators, maintainers, reviewers).
**Purpose:** trace one pull request from the moment GitHub delivers it to the moment a maintainer
approves a comment that Gjallarhorn posts.

## Request lifecycle

A triaged PR moves through these stages. Each stage is owned by one module and, where it has an
external effect, writes through the transactional outbox so nothing is sent twice and nothing is
sent before the state change commits.

```
        GitHub webhook
             |
             v
        +----------+
        |  API     |  verifies signature, dedupes X-GitHub-Delivery, records event,
        +----------+  queues triage handling in the same transaction
             |
             v
        +----------+
        | triage   |  state machine: received -> checking -> card_posted
        +----------+  (or intake_failed when rules fail)
             |         |
             |         v
             |   +-----------+     +-----------+
             |   |  Huginn   |     |   Loki    |
             |   | repro run |     |  review   |   run in parallel
             |   +-----------+     +-----------+
             |         |             |
             |         +------+----+
             |              v
             |      +----------+
             +----->|  Heimdall|  desk card + draft (or mechanical task)
                    +----------+
                           |
                    maintainer approves exact draft hash
                           |
                           v
                    +----------+
                    |Gjallarhorn|  posts approved comment to GitHub
                    +----------+
```

1. **GitHub delivers a webhook** (`pull_request.opened`, `synchronize`, `edited`, `closed`, plus
   `workflow_run` and installation lifecycle events) to `POST /webhooks/github` on the API process
   (`apps/api`). The bridge verifies the signature on the exact raw body before parsing it, records
   each `X-GitHub-Delivery` once, and queues handling inside the same transaction. A redelivered
   delivery changes nothing.
2. **Intake** (`modules/intake`) applies the repository's `heimdall.yml` rules against the PR head:
   linked issue, author assignee, required labels, open-PR limit, CLA label, CI green. One result
   per rule per head commit is stored. The PR's thread opens with `New PR #<no> <title>` — the
   title is untrusted, so it is flattened to one line, capped at `MAX_THREAD_TITLE_CHARS`, and
   stripped of Markdown and `@` syntax.
3. **Triage** (`modules/triage`) is the state machine. Only `triage.domain.next_state` may change
   `prs.state`, and only through that pure transition function (`AGENTS.md` rule 8). On `intake.done`
   with all rules passing, state moves `received -> checking` and Huginn and Loki are both enqueued.
   On any rule failing, state moves to `intake_failed` and Heimdall drafts an intake reply listing
   the failed rules. A maintainer can re-check (`maintainer.recheck`) to restart intake after fixing
   the PR.
4. **Huginn** (`agents/huginn`) runs the repository's configured reproduction command against the PR
   head and the base commit, then reports a paired verdict (`reproduced`, `not_covered`,
   `setup_failed`, `inconclusive`). Reproduction runs on GitHub Actions with `contents: read`, no
   secrets, and the validated test command passed through `env:` — never interpolated into a shell.
   At most three attempts per head; earlier attempts ending while others still run report nothing,
   so the card is never built against a partial verdict.
5. **Loki** (`agents/loki`) reviews the current diff with read-only tools and submits findings tied
   to changed lines, each cited `file:line`. At most `MAX_FINDINGS = 5`; docs-only and skip paths
   are honoured. A clean pass or a `docs_only`/`no_findings` skip still lets triage build the card.
6. **Heimdall** (`agents/heimdall`) renders a Switch summary card from the stored intake, repro and
   review results, and stores an exact text draft with a SHA-256 approval hash. Drafts never change
   GitHub on their own.
7. **Approval** (`modules/approval`) records a maintainer's authorization for the exact saved draft
   text (thumbs-up reaction on the draft post, or `/heimdall approve` in the thread) and queues the
   GitHub comment in the same transaction. Gjallarhorn rechecks the approval row and that the draft
   hash still matches immediately before posting, so an edited draft can never be posted.
8. **Gjallarhorn** (`adapters/github/sender`) posts the approved comment as the GitHub App
   installation. It is the only writer to GitHub; the GitHub reader adapter is read-only.

Both Huginn and Loki report only their own check. Triage locks the PR row, records each finishing
check's head, and moves `checking -> card_posted` only when both are done on the current head. A
push (`pull_request.synchronize`) makes both pending again and moves state back to `received`.