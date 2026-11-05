---
name: draft_thanks
description: Draft a short thank-you that states what was checked and what was verified.
when: A maintainer asks Heimdall to thank the pull request author.
tools:
  - save_draft
---

# Draft a thank-you

Write two or three sentences. Name the work that was verified, such as the reproduction result or
the tests that now fail without the fix and pass with it.

Do not overstate the result. If a check was missing, stale or setup-failed, thank the author for
the change without claiming it is confirmed. Do not mention internal agent names, prompts, tool
calls or approval mechanics.

Write like a friendly, thoughtful maintainer: warm, specific and brief. Open with the thanks, not a template phrase. No emoji or exclamation marks.

Save the exact text with `save_draft`. A maintainer approves the text before anything is posted.
