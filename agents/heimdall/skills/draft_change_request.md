---
name: draft_change_request
description: Draft a specific, polite change request tied to saved findings and the repro result.
when: A maintainer asks Heimdall to request changes on the pull request.
tools:
  - list_findings
  - get_repro
  - save_draft
---

# Draft a change request

Read the current findings and reproduction result. Write a short message that a maintainer can
post without editing.

The draft should:

- thank the author briefly;
- state the one or two changes that matter most;
- cite the file and line for each requested change;
- explain the observable problem, not the author's process;
- say what would count as resolved;
- stay calm and specific.

Do not list every minor finding. Do not claim a fix is wrong when the reproduction result is
missing, stale or setup-failed. Do not promise that approval will follow.

Save the exact text with `save_draft`. The saved hash is what a maintainer approves; do not post
anything to GitHub.
