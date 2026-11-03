# shared

The kernel every module builds on. No business rules and no I/O beyond reading settings and
writing logs. Any module may import from here.

| File | Public names |
|---|---|
| `settings.py` | `Settings`, `get_settings()`, `LogLevel`. Reads `HEIMDALL_*` env vars and `.env`; secrets are `SecretStr`. |
| `ids.py` | `InstallationId`, `RepoId`, `DraftId`, `ReproId`, `FindingId` |
| `context.py` | `TenantCtx(installation_id, request_id)`, `PrRef(repo_id, number)`, `new_request_id()` |
| `events.py` | `DomainEvent`: frozen base with `installation_id`, `occurred_at` (UTC), `name` |
| `errors.py` | `HeimdallError` (`retryable` flag) and its subclasses: `NotAMaintainer`, `DraftChanged`, `ConfigInvalid`, `RepoLimitReached`, `RepoNotEnabled`, `InstallationNotFound`, `InstallationSuspended`, `StaleHeadSha`, `ToolNotAllowed`, `GitHubUnavailable`*, `SwitchUnavailable`*, `SwitchRequestFailed` (* marks the retryable ones) |
| `logging.py` | `configure_logging(level)`, `bind_tenant(ctx, pr)`, `get_logger(name)`. Writes JSON lines; values under keys containing `token`, `secret`, `private_key`, `password`, `diff` or `authorization` are redacted. |

Tables: none.
