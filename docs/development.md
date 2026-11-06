# Development

**Audience:** contributors.
**Purpose:** how to verify a change, the test tiers and naming, and the commit/PR conventions.

## Verify before you say a change is done

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest tests/unit
```

Ruff excludes `*.md` from formatting (docs are hand-written) and `.serena` from linting. Pyright
runs in strict mode over `src`, `apps`, `agents`, `migrations`, and `tests`.

Integration tests need Postgres:

```bash
docker compose up -d postgres
uv run alembic upgrade head   # or let the app profile run migrations
DATABASE_URL=postgresql://heimdall:***@localhost:5432/heimdall uv run pytest tests/integration
```

The end-to-end replay runs the three seeded demo PRs (#3 good fix, #4 fake fix, #5 rule breaker)
through the whole pipeline: webhooks, both worker queues, the outbox, the MCP tool server, the
agents' loops, and the 👍 approval. GitHub and Switch are faked from responses recorded off the demo
repo (`tests/fixtures/e2e/`); only Loki's model reply is scripted.

```bash
DATABASE_URL=postgresql://heimdall:***@localhost:5432/heimdall uv run pytest tests/e2e -m e2e
```

CI runs lint, formatting, Pyright, unit tests, database integration tests, and the end-to-end
replay on pushes and pull requests.

## Test conventions

| Tier | What | Constraints |
|---|---|---|
| unit | `tests/unit` | Domain functions. No DB, no network, no mocks needed. |
| integration | `tests/integration` | Real Postgres (Docker Compose). Adapters tested with `respx` for HTTP; githubkit uses `httpx2`, so its tests pass an `httpx2.MockTransport` instead (see `adapters/github/README.md`). |
| e2e | `tests/e2e -m e2e` | Full-stack replay against faked GitHub/Switch fixtures. |

- Name tests after behaviour: `test_post_is_blocked_when_draft_hash_changed`.
- Every `domain.py` function has unit tests.
- Every service has at least one integration test against real Postgres.
- A bug fix comes with a test that fails before the fix.
- Never skip, disable, or loosen a test to make CI pass.

## Commit and PR conventions

- Small, focused changes. One module per PR when possible.
- Commit messages: `<module>: <what changed>`, for example
  `approval: reject stale draft hashes`.
- A PR description says what changed, why, and how it was tested, including any new dependency with
  a one-line reason.

## Do not

- Do not import across module borders except through `api.py`.
- Do not call GitHub or Switch directly from a service.
- Do not add a table without `installation_id`.
- Do not add a dependency without a one-line reason in the PR description.