# Heimdall docs

Heimdall triages open-source pull requests in a Switch/Mattermost room: plain-code rule checks at
intake, a Huginn reproduction run, a Loki read-only review, a Heimdall desk card and draft, and
maintainer-approved output through Gjallarhorn. Nothing reaches GitHub until a maintainer approves
the exact saved draft.

This folder is the single navigation page for Heimdall's documentation. Every doc below lists its
audience and a one-line summary. Scoped READMEs live beside the code they describe
(`src/heimdall/modules/*/README.md`, `src/heimdall/adapters/*/README.md`, `apps/*/README.md`,
`agents/README.md`) and are linked rather than duplicated here.

## Working rules (not documentation)

`AGENTS.md` is the house rules for anyone writing code in this repo — module borders, pure domain,
one transaction per use case, outbox for side effects, tenancy everywhere, security rules, commit
format, and test conventions. Read it before changing code.

## Docs

| Doc | Path | Audience | What it covers |
|---|---|---|---|
| How it works | `docs/how-it-works.md` | Everyone | End-to-end request lifecycle, triage state machine, draft approval, room threads. |
| Setup | `docs/setup.md` | Contributors, operators | Fresh clone to a triaged PR: Docker Compose, GitHub App, Switch/Mattermost, env vars. |
| Architecture | `docs/architecture.md` | Contributors, reviewers | Module layout, module borders, adapters and ports, outbox, job queue, state machine, tenancy. |
| Configuration | `docs/config.md` | Operators, maintainers | `heimdall.yml` layering and schema, every env var grouped by concern. |
| Security | `docs/security.md` | Everyone | Untrusted PR data, approval/draft-hash binding, workflow authorization, webhook verification, secrets. |
| Development | `docs/development.md` | Contributors | Lint/type/test commands, test tiers and naming, CI, commit/PR conventions. |
| Agents | `docs/agents.md` | Contributors | Agent runtime, skills, guardrails, ask/act flow, rate limits, memory commands. |

## Keeping docs honest

When behaviour changes, the docstring and the relevant doc above are updated in the same change.
Docs never duplicate a scoped README — they reference it. Code samples are hand-written (Markdown is
excluded from `ruff format` in `pyproject.toml`).

