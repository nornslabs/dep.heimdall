---
name: mechanical_task
description: Create one policy-gated mechanical task for a finding in an auto_suggest category.
when: A saved finding is lint, typo, rename, missing_import, docstring or changelog.
tools:
  - create_mechanical_task
  - list_findings
---

# Create a mechanical task

Load the findings and choose one that is genuinely mechanical. It must be small, local and safe:
no behaviour change, no public API change, no data or security change.

Create exactly one task with `create_mechanical_task`. Include:

- the finding id and its cited `file:line`;
- the category;
- the smallest concrete change that resolves it;
- the tests that should still pass.

Do not bundle several findings into one task. Do not include unrelated cleanup. If the category is
uncertain or the change would touch behaviour, do not create a task; report the finding normally.

The task is a draft under the `auto_suggest` policy, not a GitHub write. A maintainer still
controls approval, and the hash rule still applies to any posted text.
