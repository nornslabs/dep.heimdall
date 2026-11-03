"""Config layers and how they combine: defaults < org .github repo < the repo itself."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeGuard

ConfigValues = Mapping[str, object]


def is_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    """Whether a parsed YAML value is a mapping (a YAML "dict")."""
    return isinstance(value, Mapping)


@dataclass(frozen=True)
class ConfigLayer:
    """One parsed heimdall.yml and where it came from, for error messages.

    Attributes:
        source: Shown in errors, for example ``heimdall.yml`` or ``.github/heimdall.yml``.
        values: The file's top-level mapping.
    """

    source: str
    values: ConfigValues


def merge_layers(base: ConfigValues, override: ConfigValues) -> dict[str, object]:
    """Lay ``override`` on top of ``base`` without changing either.

    Mappings merge key by key at every depth; anything else (lists included) in ``override``
    replaces the base value, so a repo can shorten a list the org set.

    Args:
        base: The lower layer.
        override: The higher layer.

    Returns:
        A new merged mapping.
    """
    merged: dict[str, object] = dict(base)
    for key, value in override.items():
        lower = merged.get(key)
        if is_mapping(lower) and is_mapping(value):
            merged[key] = merge_layers(_as_values(lower), _as_values(value))
        else:
            merged[key] = value
    return merged


def _as_values(mapping: Mapping[object, object]) -> ConfigValues:
    """Narrow a parsed mapping to string keys; parse_layer already rejected other keys."""
    return {str(key): value for key, value in mapping.items()}
