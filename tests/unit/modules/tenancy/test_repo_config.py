"""RepoConfig parses heimdall.yml, fills defaults, and rejects mistakes with exact errors."""

import pydantic
import pytest

from heimdall.modules.tenancy.domain import RepoConfig, build_config, parse_layer
from heimdall.shared.errors import ConfigInvalid

REPO_FILE = "heimdall.yml"

LLD_EXAMPLE = """
version: 1
maintainers: [ravi, meera]
intake:
  require_linked_issue: true
  require_assignee: true
  required_labels: [hackathon]
  max_open_prs_per_author: 5
  require_cla: false
  require_ci_green: true
repro:
  test_cmd: "pytest -q"
  allowed_runners: [pytest]
  source_paths: [src/]
  workflow: heimdall-repro.yml
review:
  max_findings: 5
  skip_paths: ["docs/**", "*.md"]
auto_suggest:
  enabled: true
  categories: [lint, typo, rename, missing_import, docstring, changelog]
room:
  switch_room: triage
"""


def config_from(text: str) -> RepoConfig:
    return build_config([parse_layer(text, REPO_FILE)])


def error_from(text: str) -> str:
    with pytest.raises(ConfigInvalid) as raised:
        config_from(text)
    return str(raised.value)


# --- valid files and defaults ---------------------------------------------------------


def test_empty_file_gives_the_defaults() -> None:
    config = config_from("")

    assert config.version == 1
    assert config.maintainers == ()
    assert config.intake.required_labels == ()
    assert config.intake.max_open_prs_per_author == 5
    assert config.repro.test_cmd == "pytest -q"
    assert config.review.max_findings == 5
    assert config.auto_suggest.categories == (
        "lint", "typo", "rename", "missing_import", "docstring", "changelog",
    )  # fmt: skip
    assert config.room.switch_room == "triage"


def test_null_file_counts_as_empty() -> None:
    assert config_from("~\n") == config_from("")


def test_lld_example_parses_to_the_same_values() -> None:
    config = config_from(LLD_EXAMPLE)

    assert config.maintainers == ("ravi", "meera")
    assert config.intake.required_labels == ("hackathon",)
    assert config.repro.allowed_runners == ("pytest",)
    assert config.repro.source_paths == ("src/",)
    assert config.review.skip_paths == ("docs/**", "*.md")


def test_missing_section_keeps_its_defaults() -> None:
    config = config_from("intake:\n  require_cla: true\n")

    assert config.intake.require_cla is True
    assert config.intake.require_assignee is True
    assert config.repro.workflow == "heimdall-repro.yml"


def test_config_is_immutable() -> None:
    config = config_from("")

    with pytest.raises(pydantic.ValidationError):
        config.version = 2  # pyright: ignore[reportAttributeAccessIssue]


# --- unknown keys -----------------------------------------------------------------------


def test_unknown_top_level_key_is_rejected_with_suggestion() -> None:
    assert error_from("intak:\n  require_cla: true\n") == (
        "heimdall.yml is invalid:\n- intak: unknown key (did you mean 'intake'?)"
    )


def test_unknown_nested_key_is_rejected_with_its_path() -> None:
    assert error_from("intake:\n  require_clas: true\n") == (
        "heimdall.yml is invalid:\n- intake.require_clas: unknown key (did you mean 'require_cla'?)"
    )


def test_unknown_key_with_no_close_match_has_no_suggestion() -> None:
    assert error_from("colour: blue\n") == "heimdall.yml is invalid:\n- colour: unknown key"


def test_all_problems_are_reported_together() -> None:
    text = "intake:\n  max_open_prs_per_author: 0\nreview:\n  max_findngs: 3\n"

    assert error_from(text) == (
        "heimdall.yml is invalid:\n"
        "- intake.max_open_prs_per_author: must be at least 1\n"
        "- review.max_findngs: unknown key (did you mean 'max_findings'?)"
    )


# --- bad values -------------------------------------------------------------------------


def test_max_open_prs_must_be_positive() -> None:
    assert error_from("intake:\n  max_open_prs_per_author: 0\n") == (
        "heimdall.yml is invalid:\n- intake.max_open_prs_per_author: must be at least 1"
    )


def test_max_findings_cannot_exceed_loki_limit() -> None:
    assert error_from("review:\n  max_findings: 9\n") == (
        "heimdall.yml is invalid:\n- review.max_findings: must be at most 5"
    )


def test_unknown_runner_is_rejected() -> None:
    text = "repro:\n  allowed_runners: [pytest, cargo test]\n"

    assert error_from(text) == (
        "heimdall.yml is invalid:\n"
        "- repro.allowed_runners.1: must be one of 'pytest', 'npm test' or 'go test'"
    )


def test_unknown_auto_suggest_category_is_rejected() -> None:
    text = "auto_suggest:\n  categories: [lint, refactor]\n"

    assert error_from(text) == (
        "heimdall.yml is invalid:\n"
        "- auto_suggest.categories.1: must be one of 'lint', 'typo', 'rename', "
        "'missing_import', 'docstring' or 'changelog'"
    )


def test_wrong_type_is_rejected() -> None:
    assert error_from("intake:\n  require_cla: sometimes\n") == (
        "heimdall.yml is invalid:\n- intake.require_cla: must be true or false"
    )


def test_section_that_is_not_a_mapping_is_rejected() -> None:
    assert error_from("intake: yes\n") == (
        "heimdall.yml is invalid:\n- intake: must be a mapping of keys to values"
    )


def test_unsupported_version_is_rejected() -> None:
    assert error_from("version: 2\n") == ("heimdall.yml is invalid:\n- version: must be one of 1")


# --- test_cmd may narrow the runner, never replace it ---------------------------------


@pytest.mark.parametrize("test_cmd", ["pytest", "pytest -q tests/test_api.py", "pytest -k x"])
def test_test_cmd_may_narrow_the_runner(test_cmd: str) -> None:
    assert config_from(f'repro:\n  test_cmd: "{test_cmd}"\n').repro.test_cmd == test_cmd


def test_test_cmd_must_start_with_an_allowed_runner() -> None:
    assert error_from('repro:\n  test_cmd: "rm -rf /"\n') == (
        "heimdall.yml is invalid:\n- repro: test_cmd must start with an allowed runner: 'pytest'"
    )


def test_test_cmd_runner_prefix_needs_a_word_boundary() -> None:
    assert "test_cmd must start with an allowed runner" in error_from(
        'repro:\n  test_cmd: "pytestfoo"\n'
    )


def test_multi_word_runner_is_matched_whole() -> None:
    text = 'repro:\n  allowed_runners: [npm test]\n  test_cmd: "npm test -- --watch=false"\n'

    assert config_from(text).repro.test_cmd == "npm test -- --watch=false"


# --- YAML problems ----------------------------------------------------------------------


def test_yaml_syntax_error_reports_the_line() -> None:
    assert error_from("intake:\n  require_cla: [true\n") == (
        "heimdall.yml is not valid YAML (line 3)"
    )


def test_top_level_list_is_rejected() -> None:
    assert error_from("- intake\n") == (
        "heimdall.yml is invalid:\n- (top level): must be a mapping of keys to values"
    )


def test_non_string_key_is_rejected() -> None:
    assert error_from("1: x\n") == "heimdall.yml is invalid:\n- 1: keys must be text"
