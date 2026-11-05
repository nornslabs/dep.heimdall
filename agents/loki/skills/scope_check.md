---
name: scope_check
description: Check the diff against the linked issue and flag unrelated or missing changes.
when: Reviewing whether the pull request does the requested work and nothing unrelated.
tools:
  - get_pr_context
  - get_diff
---

# Check the scope

Read the PR context to find the linked issue and its stated goal. Then read the diff and compare
the changed files with that goal.

Report a finding when:

- a changed file is unrelated to the issue and has no supporting explanation;
- the diff changes behaviour the issue did not ask for and increases risk;
- the issue requires a change that is missing from the diff;
- generated, vendored or formatting-only files hide the real change.

Do not flag a necessary supporting change, a test, a migration or a documentation update that the
issue clearly requires. Do not reject a PR merely because it is larger than expected.

Cite `file:line` for every finding. Explain the issue requirement and the specific gap or unrelated
change. Keep the review about the code, never about the author.
