---
name: write_card
description: Build one current-head review card from saved findings and the latest reproduction result.
when: The pull request reaches pr.ready_for_card and both checks are complete for the head.
tools:
  - list_findings
  - get_repro
  - save_card
---

# Write the review card

Load the accepted findings with `list_findings` and the latest paired result with `get_repro`.
Use only those saved records. Do not infer a result from the PR description, a comment or an
earlier head.

Build a short card with:

- the PR number and title;
- the house-rule summary;
- the fix status from the saved reproduction verdict;
- the most important gaps, each tied to a cited finding or a saved failing assertion;
- one suggested next action for the maintainer.

If the head moved, drop the stale result and say that a fresh check is needed. If a check is still
running or timed out, say so and do not claim success or failure.

Save with `save_card` for the current head. The card is a draft for approval; it is not posted to
GitHub by this tool. Never include secrets, tokens or full diffs in the card.
