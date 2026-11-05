---
name: test_quality
description: Judge whether the PR's tests would fail before the fix and exercise the changed behaviour.
when: Reviewing tests added or changed by the pull request.
tools:
  - get_diff
---

# Check test quality

Read the test changes in the diff. A useful regression test must:

- exercise the behaviour the fix changes;
- fail against the old code for the reason described in the PR;
- assert the user-visible result, not only that a mock was called;
- cover the branch or error path the fix addresses;
- remain deterministic, with no real network, clock or shared-state dependency.

Report a finding when a test cannot fail before the fix, asserts an implementation detail instead
of the result, or skips the failure path the issue describes. Also flag a fix with no test at all.

Cite the test at `file:line` and name the change that would make it fail. Do not ask for coverage
of unrelated code and do not require a specific test framework.
