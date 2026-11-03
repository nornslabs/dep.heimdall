"""Pure tenancy rules: parse heimdall.yml layers and build a repo's validated config (no I/O)."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import yaml
from pydantic import ValidationError

from heimdall.modules.tenancy._config_errors import config_invalid, describe_errors
from heimdall.modules.tenancy._config_merge import (
    ConfigLayer,
    ConfigValues,
    is_mapping,
    merge_layers,
)
from heimdall.modules.tenancy._config_model import (
    MAX_FINDINGS,
    AutoSuggestConfig,
    IntakeConfig,
    RepoConfig,
    ReproConfig,
    ReviewConfig,
    RoomConfig,
)
from heimdall.shared.errors import ConfigInvalid

__all__ = [
    "CONFIG_PATH",
    "MAX_FINDINGS",
    "ORG_CONFIG_REPO",
    "AutoSuggestConfig",
    "ConfigFile",
    "ConfigLayer",
    "IntakeConfig",
    "RepoConfig",
    "ReproConfig",
    "ReviewConfig",
    "RoomConfig",
    "build_config",
    "config_sha",
    "merge_layers",
    "parse_layer",
]

NOT_A_MAPPING = "must be a mapping of keys to values"

CONFIG_PATH = "heimdall.yml"
"""Where heimdall.yml lives, both in a repo and in the org's ``.github`` repo."""

ORG_CONFIG_REPO = ".github"
"""The org-wide repository whose heimdall.yml sits between the defaults and each repo."""


@dataclass(frozen=True)
class ConfigFile:
    """One heimdall.yml as fetched, before parsing.

    Attributes:
        source: Shown in errors, for example ``heimdall.yml``.
        text: The file's raw contents.
    """

    source: str
    text: str


def config_sha(files: Sequence[ConfigFile]) -> str:
    """Fingerprint the files a config is built from, to cache the built config.

    The same files in the same order always give the same value, whatever commit they were
    read at, so a cached config is reused until a file actually changes.

    Args:
        files: The fetched files, lowest precedence first.

    Returns:
        A 64-character hex SHA-256.
    """
    encoded = json.dumps([[file.source, file.text] for file in files], ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def parse_layer(text: str, source: str) -> ConfigLayer:
    """Read one heimdall.yml file into a layer, without validating its keys yet.

    An empty or ``null`` file is an empty layer, so a repo can opt in with a blank file.

    Args:
        text: The file's contents, as fetched at the PR's head commit.
        source: Where it came from, shown in errors.

    Returns:
        The file's top-level mapping, tagged with its source.

    Raises:
        ConfigInvalid: the file is not YAML, its top level is not a mapping, or a key is not text.
    """
    try:
        parsed: object = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ConfigInvalid(f"{source} is not valid YAML{_line_of(error)}") from error
    if parsed is None:
        return ConfigLayer(source=source, values={})
    if not is_mapping(parsed):
        raise config_invalid(source, [f"(top level): {NOT_A_MAPPING}"])
    return ConfigLayer(source=source, values=_text_keys(parsed, source))


def build_config(layers: Sequence[ConfigLayer]) -> RepoConfig:
    """Merge layers over the defaults (lowest first) and validate the result.

    Each layer is checked on its own first, so an error names the file that caused it; rules
    between fields are then checked on the merged config.

    Args:
        layers: Parsed files, lowest precedence first (org ``.github``, then the repo).

    Returns:
        The validated effective config.

    Raises:
        ConfigInvalid: a layer, or the merged config, breaks the schema.
    """
    merged: dict[str, object] = {}
    for layer in layers:
        _validate(layer.values, layer.source)
        merged = merge_layers(merged, layer.values)
    return _validate(merged, _effective_label(layers))


def _validate(values: ConfigValues, source: str) -> RepoConfig:
    try:
        return RepoConfig.model_validate(values)
    except ValidationError as error:
        raise config_invalid(source, describe_errors(error, RepoConfig)) from error


def _effective_label(layers: Sequence[ConfigLayer]) -> str:
    chain = " < ".join(["defaults", *(layer.source for layer in layers)])
    return f"effective config ({chain})"


def _text_keys(parsed: Mapping[object, object], source: str) -> dict[str, object]:
    """Reject non-text keys at any depth, so every later lookup can assume strings."""
    bad_keys = _non_text_keys(parsed, prefix="")
    if bad_keys:
        raise config_invalid(source, [f"{key}: keys must be text" for key in bad_keys])
    return {str(key): value for key, value in parsed.items()}


def _non_text_keys(mapping: Mapping[object, object], prefix: str) -> list[str]:
    found: list[str] = []
    for key, value in mapping.items():
        path = f"{prefix}{key}"
        if not isinstance(key, str):
            found.append(path)
        elif is_mapping(value):
            found += _non_text_keys(value, prefix=f"{path}.")
    return found


def _line_of(error: yaml.YAMLError) -> str:
    mark = getattr(error, "problem_mark", None)
    line = getattr(mark, "line", None)
    return f" (line {line + 1})" if isinstance(line, int) else ""
