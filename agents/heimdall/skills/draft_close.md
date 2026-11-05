---
name: draft_close
description: Draft a close message that gives the reason and cites the saved evidence.
when: A maintainer asks Heimdall to close the pull request.
tools:
  - save_draft
---

# Draft a close message

State the decision in the first sentence, then give the reason. A close message is not a review
essay; two or three short paragraphs are enough.

Use saved evidence only:

- the reproduction verdict and its completed attempt count;
- the accepted findings and their cited lines;
- the linked issue, when the change does not address it.

Say whether the author may reopen with a narrower change. Do not blame the author, guess at intent
or mention internal agent names or approval mechanics.

If the evidence is incomplete, say what is missing instead of inventing a reason. Save the exact
text with `save_draft`; a maintainer approves it before it is posted.
