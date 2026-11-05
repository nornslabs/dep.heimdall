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

Write the card the way a colleague would summarise the PR to a maintainer in chat: plain
sentences, first person is fine, no form fields. Cover:

- what the PR does, in one sentence of your own words (the intake summary);
- what the tests showed, from the saved reproduction verdict (the repro summary);
- only the gaps that matter, each tied to a cited finding or a saved failing assertion.

Leave findings empty when there are none. Never write filler such as "No specific gaps
identified" or "Suggested action:"; if there is nothing to flag, say what looks fine instead.

If the head moved, drop the stale result and say that a fresh check is needed. If a check is still
running or timed out, say so and do not claim success or failure.

Save with `save_card` for the current head. The card is a draft for approval; it is not posted to
GitHub by this tool. Never include secrets, tokens or full diffs in the card.
