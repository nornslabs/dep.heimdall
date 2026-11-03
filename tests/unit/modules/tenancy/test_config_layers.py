"""Config layers merge defaults < org .github < repo, and errors name their layer."""

import copy

import pytest

from heimdall.modules.tenancy.domain import build_config, merge_layers, parse_layer
from heimdall.shared.errors import ConfigInvalid

ORG_FILE = ".github/heimdall.yml"
REPO_FILE = "heimdall.yml"


def test_repo_overrides_org_overrides_defaults() -> None:
    org = parse_layer("intake:\n  max_open_prs_per_author: 3\n  require_cla: true\n", ORG_FILE)
    repo = parse_layer("intake:\n  max_open_prs_per_author: 8\n", REPO_FILE)

    config = build_config([org, repo])

    assert config.intake.max_open_prs_per_author == 8
    assert config.intake.require_cla is True
    assert config.intake.require_assignee is True


def test_nested_sections_merge_key_by_key() -> None:
    org = parse_layer("repro:\n  source_paths: [lib/]\n", ORG_FILE)
    repo = parse_layer("repro:\n  workflow: ci-repro.yml\n", REPO_FILE)

    config = build_config([org, repo])

    assert config.repro.source_paths == ("lib/",)
    assert config.repro.workflow == "ci-repro.yml"


def test_lists_are_replaced_not_joined() -> None:
    org = parse_layer("maintainers: [ravi, meera]\n", ORG_FILE)
    repo = parse_layer("maintainers: [asha]\n", REPO_FILE)

    assert build_config([org, repo]).maintainers == ("asha",)


def test_no_layers_gives_the_defaults() -> None:
    assert build_config([]).intake.max_open_prs_per_author == 5


def test_error_names_the_layer_it_came_from() -> None:
    org = parse_layer("intake:\n  require_clas: true\n", ORG_FILE)
    repo = parse_layer("", REPO_FILE)

    with pytest.raises(ConfigInvalid) as raised:
        build_config([org, repo])

    assert str(raised.value) == (
        ".github/heimdall.yml is invalid:\n"
        "- intake.require_clas: unknown key (did you mean 'require_cla'?)"
    )


def test_cross_field_error_after_merge_is_labelled_effective_config() -> None:
    org = parse_layer("repro:\n  allowed_runners: [go test]\n  test_cmd: go test ./...\n", ORG_FILE)
    repo = parse_layer('repro:\n  test_cmd: "pytest -q"\n', REPO_FILE)

    with pytest.raises(ConfigInvalid) as raised:
        build_config([org, repo])

    assert str(raised.value) == (
        "effective config (defaults < .github/heimdall.yml < heimdall.yml) is invalid:\n"
        "- repro: test_cmd must start with an allowed runner: 'go test'"
    )


def test_merge_does_not_change_its_inputs() -> None:
    base: dict[str, object] = {"intake": {"require_cla": False, "required_labels": ["a"]}}
    override: dict[str, object] = {"intake": {"require_cla": True}}
    base_before, override_before = copy.deepcopy(base), copy.deepcopy(override)

    merged = merge_layers(base, override)

    assert merged == {"intake": {"require_cla": True, "required_labels": ["a"]}}
    assert base == base_before
    assert override == override_before
