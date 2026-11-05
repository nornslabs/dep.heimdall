---
name: judge_repro
description: Decide from saved paired outcomes whether the fix reproduced, was not covered, or failed setup.
when: A paired reproduction attempt finished and both saved outcomes are available.
tools:
  - get_repro
---

# Judge the reproduction

Read the saved result with `get_repro`. Judge only from saved data: the completed attempt count,
the pass counts with and without the fix, and the saved failure details.

Choose one outcome:

- **Reproduced**: the failing tests failed without the fix and passed with it, in the same attempt,
  with the same command and head.
- **Not covered**: the tests passed in both modes, or the changed behaviour is not exercised.
  Say which test or assertion was expected to fail and did not.
- **Setup failed**: the run did not establish a valid baseline, for example a missing dependency,
  an invalid command, a collection error or a timeout. Do not call this a fix failure.

Never claim a fix is confirmed from a partial run, an older head, a timed-out attempt or a single
mode. If the attempt is stale or still running, say so and withhold the verdict.

Report completed attempts, both pass counts and at most five saved failing assertions.
