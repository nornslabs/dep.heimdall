# CLAUDE.md

@AGENTS.md

## Workflow for every task

1. **Plan first.** Before writing code for a Linear issue, read the issue and the relevant part of
   the LLD (Notion: "Heimdall LLD"), then write a short plan: files to add or change, tests to
   write first, decisions the issue leaves open, and how it will be verified. Present it and wait
   for approval before implementing.
2. **Then implement**, tests first (RED, then GREEN), on the branch name Linear generates.
3. **Verify** with `ruff`, `pyright`, `pytest tests/unit` and, when the change touches the
   database, `pytest tests/integration` against the compose Postgres.
4. **Open a PR** that says what changed, why, and how it was tested, including any new
   dependency with a one-line reason.
