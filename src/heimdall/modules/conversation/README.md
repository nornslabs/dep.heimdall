# conversation

Limits on how much an agent talks back. Today that is one rule: a sender gets at most
`MAX_QUESTIONS_PER_WINDOW` (20) answered questions per agent, per PR, in any rolling
`QUESTION_WINDOW` (one hour). Past that, the agent sends one short notice, then stays silent
until the allowance comes back. A refused question never reaches the model.

Public functions: `admit_question_in_transaction`, plus the pure rule `decide_question` and the
types `QuestionScope`, `Admitted`, `Limited` and `QuestionDecision`.

| File | Role |
|---|---|
| `api.py` | Public interface (the only file other modules import) |
| `service.py` | Use case: lock the sender's scope, read the window, decide, save the row |
| `domain.py` | Pure rules: the limit, the window and `decide_question` |
| `models.py` | Persistence model |

Tables (`models.py`): `agent_question` (one row per question put to an agent; unique per
`(installation_id, agent, message_id)`; indexed on
`(installation_id, agent, sender, asked_at)`).

- A question counts against `(agent, sender, PR)`. Outside a PR thread it counts against
  `(agent, sender, room)`. Sender names are compared ignoring case.
- Every question is saved, with an outcome of `answered`, `limited` (this refusal carried the
  notice) or `limited_silent`. Only `answered` rows count toward the limit.
- The notice goes out once per run of refusals: a `limited` row newer than the sender's last
  answer means it was already sent. When the allowance returns and the sender fills it again,
  they get a new notice.
- Switch can deliver a message twice. The second delivery gets the first decision back and is
  never counted again. A repeated refusal never carries a second notice.
- `admit_question_in_transaction` takes a transaction-scoped Postgres advisory lock on the
  scope, so two questions arriving together cannot both slip under the limit. It never commits;
  the caller writes the notice (outbox) and its audit entry in the same transaction.

Memory commands (`remember`, `forget`, `what do you remember`) do not count: plain code answers
them without a model call.
