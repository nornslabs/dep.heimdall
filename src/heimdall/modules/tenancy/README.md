# tenancy

Installations, repos and each repo's effective `heimdall.yml` config.

Repository auto-suggest pauses are tenant-scoped and persist until policy is re-enabled.

| File | Role |
|---|---|
| `api.py` | `on_installation_event`, `get_tenant`, `load_config`, `InstallationEvent`, and `RepoConfig` |
| `service.py` | Use cases |
| `domain.py` | `parse_layer(text, source)`, `build_config(layers)`, `merge_layers(base, override)`, `RepoConfig` and its sections. Pure, no I/O |
| `_config_model.py` | The schema: sections, defaults, the `test_cmd` rule |
| `_config_merge.py` | `ConfigLayer` and the deep merge |
| `_config_errors.py` | Turns pydantic errors into the exact lines maintainers see |
| `ports.py` | Adapter interfaces |
| `models.py` | `installations`, `repos` |

Installation events upsert tenant and repo records. Removed repositories are disabled;
suspension blocks the tenant, and deletion disables its repos. `get_tenant` rejects
unknown or suspended installations. `load_config` reads `.github/heimdall.yml` and then
`heimdall.yml` at the PR head SHA and caches the merged config by `config_sha`.

`load_config` reads `.github/heimdall.yml` then `heimdall.yml` at the PR head SHA,
validates their merged values, and caches the result in the tenant's repo row keyed by
`config_sha`.

## heimdall.yml

Read at the PR's head commit from three layers, each overriding the one before: Heimdall's
defaults, then the org's `.github` repo, then the repo itself.

- Mappings merge key by key. Lists and single values are **replaced**, so a repo can shorten
  a list the org set.
- Unknown keys are errors, so typos are caught (with a "did you mean" hint).
- `maintainers` defaults to empty, so nobody can command the Desk until a repo names them.
- `repro.test_cmd` must start with one of `repro.allowed_runners`. Huginn may narrow the
  command, never replace the runner.
- `review.max_findings` can't exceed Loki's limit of 5.

Defaults:

```yaml
version: 1
maintainers: []
intake: {require_linked_issue: true, require_assignee: true, required_labels: [],
         max_open_prs_per_author: 5, require_cla: false, require_ci_green: true}
repro: {test_cmd: "pytest -q", allowed_runners: [pytest], source_paths: [src/],
        workflow: heimdall-repro.yml}
review: {max_findings: 5, skip_paths: ["docs/**", "*.md"]}
auto_suggest: {enabled: true,
               categories: [lint, typo, rename, missing_import, docstring, changelog]}
room: {switch_room: triage}
```

## Errors

Every problem raises `ConfigInvalid` (from `shared.errors`), with all of a file's problems in
one message, naming the file:

```
heimdall.yml is invalid:
- intake.max_open_prs_per_author: must be at least 1
- review.max_findngs: unknown key (did you mean 'max_findings'?)
```

Rules that span fields are checked after merging, labelled
`effective config (defaults < .github/heimdall.yml < heimdall.yml)`. Intake shows this text
as-is when a config is invalid.
