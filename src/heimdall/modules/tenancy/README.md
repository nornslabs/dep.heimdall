# tenancy

Installations, repos and each repo's effective `heimdall.yml` config.

| File | Role |
|---|---|
| `api.py` | Public interface: `load_config`, `TenancyDeps`, `RepoConfig` |
| `service.py` | `load_config(deps, ctx, repo_id, sha) -> RepoConfig` |
| `domain.py` | `parse_layer(text, source)`, `build_config(layers)`, `merge_layers(base, override)`, `config_sha(files)`, `RepoConfig` and its sections. Pure, no I/O |
| `_config_model.py` | The schema: sections, defaults, the `test_cmd` rule |
| `_config_merge.py` | `ConfigLayer` and the deep merge |
| `_config_errors.py` | Turns pydantic errors into the exact lines maintainers see |
| `ports.py` | `ConfigFileReader` (implemented by `adapters/github`) |
| `models.py` | `installations`, `repos` |

## heimdall.yml

Three layers, each overriding the one before:

1. Heimdall's defaults.
2. `heimdall.yml` in the org's `<owner>/.github` repo, read at its **default branch** (that repo
   has no copy of the PR's commit). Errors call it `<owner>/.github: heimdall.yml`.
3. `heimdall.yml` at the repo root, read at the PR's **head commit**.

A missing file is skipped; no files at all gives the defaults.

`load_config` fetches both files every time, because a push can edit heimdall.yml. It then
compares their fingerprint (`config_sha`, a SHA-256 of each file's source and text) with the one
stored on the `repos` row. If they match, the stored `repos.config` is returned and nothing is
written; otherwise the config is rebuilt and stored with its new `config_sha`. An invalid config
raises and stores nothing, so the error shows up on every check. A stored config that no longer
validates (after a schema change) is rebuilt.

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
