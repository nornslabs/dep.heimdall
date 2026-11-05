---
name: mechanical_tagging
description: Decide whether a finding is a mechanical category that policy may fix automatically.
when: Saving a finding that may belong to an auto_suggest category.
tools:
  - save_findings
---

# Tag a mechanical finding

A mechanical finding is small, local and safe for policy to fix without a reviewer rewriting the
change. Use exactly one of these categories when it fits:

- `lint`: a rule the repository's linter already enforces.
- `typo`: a misspelling in text, a comment or a name.
- `rename`: a local rename with no behaviour change.
- `missing_import`: an import required for code that already exists.
- `docstring`: a missing or stale docstring on changed public code.
- `changelog`: a required changelog entry for a user-visible change.

Do not tag a finding as mechanical when it changes behaviour, public API, data, security, tests or
configuration semantics. When in doubt, leave the category empty and describe the finding normally.

Save findings only through `save_findings`. Never invent a category and never mark a finding
mechanical to make it easier to apply. The policy gate still decides whether a task is created.
