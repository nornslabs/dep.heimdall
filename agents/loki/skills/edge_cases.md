---
name: edge_cases
description: Look for inputs and states the change does not handle, and cite each finding.
when: Reviewing a diff for unhandled inputs, empty values, limits or failure paths.
tools:
  - get_pr_context
  - get_diff
---

# Check edge cases

Read the PR context and the diff. For each changed function or branch, ask what happens when:

- a value is empty, missing, zero, negative or at its maximum;
- a collection is empty or has one item;
- an external call fails, times out or returns an unexpected shape;
- two requests or jobs run at the same time;
- a user repeats an action or retries after a partial success.

Only report a case the change actually touches or makes worse. Do not list hypothetical hardening
for untouched code.

Every finding must cite `file:line` in the changed diff, state the input or state that breaks it,
and describe the observable wrong result. If the code already handles the case, do not report it.

Prefer a few concrete findings over a long list. Do not describe how the code was written or make
guesses about the author.
