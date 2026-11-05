---
name: explain_why
description: Explain the current card's verdict and gaps from saved findings and repro data.
when: A maintainer asks Heimdall why the pull request received its current result.
tools:
  - list_findings
  - get_repro
---

# Explain the result

Answer from the saved records, not from memory of an earlier run. Load the findings and the
reproduction result for the current head.

Structure the answer as:

1. the verdict in one sentence;
2. the evidence: completed paired attempts, pass counts and cited findings;
3. what is missing or uncertain, such as a stale head or a setup failure;
4. what would change the verdict.

Quote only short snippets needed to identify a finding. Do not dump a diff, test log or full
finding list. If the saved data does not support a clear answer, say which record is missing.

This is an explanation, not a new approval. Do not claim that anything was posted to GitHub.
