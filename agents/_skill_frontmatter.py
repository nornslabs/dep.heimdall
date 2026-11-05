"""Read one skill markdown file: its frontmatter, declared tools and playbook body.

Every rejection carries a file name and a rule, never the rejected value, so a broken skill
can be fixed from the log without copying untrusted content into it.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

MAX_DESCRIPTION_LENGTH = 160
MAX_WHEN_LENGTH = 200
MAX_SKILL_CHARACTERS = 8_000
SKILL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
FRONTMATTER_FIELDS = frozenset({"name", "description", "when", "tools"})
_MISSING_OPENING = "frontmatter is missing its opening '---' line"
_MISSING_CLOSING = "frontmatter is missing its closing '---' line"


@dataclass(frozen=True)
class Skill:
    """One validated playbook the model can load by name.

    Attributes:
        name: Lowercase identifier, also the filename stem.
        description: One line shown in the system prompt index.
        when: Trigger text for authors and tests; not sent to the model.
        tools: Tools the playbook may use, all on the agent's allow-list.
        body: Playbook text with the frontmatter removed.
    """

    name: str
    description: str
    when: str
    tools: tuple[str, ...]
    body: str


def read_skill(path: Path, allowed_tools: frozenset[str]) -> tuple[Skill | None, list[str]]:
    """Read and validate one skill file.

    Args:
        path: The markdown file to read.
        allowed_tools: The agent's MCP allow-list.

    Returns:
        The skill and an empty error list, or None and one message per broken rule.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return None, [f"{path.name}: cannot read file ({type(error).__name__})"]
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    try:
        frontmatter, body = _split_frontmatter(normalized)
    except ValueError as error:
        return None, [f"{path.name}: {error}"]
    values = _yaml_mapping(frontmatter, path.name)
    if isinstance(values, list):
        return None, values
    errors = _validate_fields(values, body, path, allowed_tools)
    if errors:
        return None, [f"{path.name}: {message}" for message in errors]
    return _build_skill(values, body), []


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return the YAML block and the body, or raise ValueError naming the missing line."""
    if not text.startswith("---\n"):
        raise ValueError(_MISSING_OPENING)
    closing = text.find("\n---\n", len("---\n"))
    if closing == -1:
        raise ValueError(_MISSING_CLOSING)
    return text[len("---\n") : closing], text[closing + len("\n---\n") :]


def _yaml_mapping(frontmatter: str, filename: str) -> dict[str, object] | list[str]:
    """Parse the YAML block into string-keyed values, or describe why it cannot be."""
    try:
        parsed = yaml.safe_load(frontmatter)
    except yaml.YAMLError as error:
        return [f"{filename}: frontmatter is not valid YAML ({_yaml_location(error)})"]
    if not isinstance(parsed, dict):
        return [f"{filename}: frontmatter must be a YAML mapping"]
    values: dict[str, object] = {}
    errors: list[str] = []
    for key, value in parsed.items():  # type: ignore[misc]
        if not isinstance(key, str):
            errors.append(f"{filename}: frontmatter keys must be strings")
            continue
        values[key] = value
    return errors or values


def _yaml_location(error: yaml.YAMLError) -> str:
    """Name the YAML error's position without echoing the offending line."""
    mark = getattr(error, "problem_mark", None)
    if mark is None:
        return type(error).__name__
    return f"line {mark.line + 1}, column {mark.column + 1}"


def _validate_fields(
    values: dict[str, object], body: str, path: Path, allowed_tools: frozenset[str]
) -> list[str]:
    """Check every frontmatter field, returning one message per broken rule."""
    errors: list[str] = []
    for field in sorted(FRONTMATTER_FIELDS - values.keys()):
        errors.append(f"missing required field {field!r}")
    for field in sorted(values.keys() - FRONTMATTER_FIELDS):
        errors.append(f"unknown field {field!r}")
    name = _text_field(values, "name", errors)
    description = _text_field(values, "description", errors)
    when = _text_field(values, "when", errors)
    if name is not None:
        _check_name(name, path, errors)
    if description is not None:
        _check_length("description", description, MAX_DESCRIPTION_LENGTH, errors)
    if when is not None:
        _check_length("when", when, MAX_WHEN_LENGTH, errors)
    _check_tools(values, allowed_tools, errors)
    _check_body(body, errors)
    return errors


def _text_field(values: dict[str, object], field: str, errors: list[str]) -> str | None:
    """Return a trimmed single-line string field, or record why it is unusable."""
    value = values.get(field)
    if not isinstance(value, str):
        errors.append(f"{field!r} must be a string")
        return None
    trimmed = value.strip()
    if not trimmed:
        errors.append(f"{field!r} must not be empty")
        return None
    if "\n" in trimmed:
        errors.append(f"{field!r} must be a single line")
        return None
    return trimmed


def _check_name(name: str, path: Path, errors: list[str]) -> None:
    """Require a lowercase identifier that matches the filename stem."""
    if not SKILL_NAME_PATTERN.fullmatch(name):
        errors.append("'name' must match ^[a-z][a-z0-9_]*$")
    if name != path.stem:
        errors.append("'name' must equal the filename stem")


def _check_length(field: str, value: str, limit: int, errors: list[str]) -> None:
    """Reject a field longer than its prompt-friendly cap."""
    if len(value) > limit:
        errors.append(f"{field!r} must be at most {limit} characters")


def _check_tools(
    values: dict[str, object], allowed_tools: frozenset[str], errors: list[str]
) -> None:
    """Require a list of unique tool names, each on the agent's allow-list."""
    raw = values.get("tools")
    if not isinstance(raw, list):
        errors.append("'tools' must be a list")
        return
    items = cast(list[object], raw)
    names = [item for item in items if isinstance(item, str)]
    if len(names) != len(items):
        errors.append("'tools' must contain only strings")
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        errors.append(f"'tools' has duplicate names: {', '.join(duplicates)}")
    outside = sorted(set(names) - allowed_tools)
    if outside:
        errors.append(f"'tools' names tools outside the allow-list: {', '.join(outside)}")


def _check_body(body: str, errors: list[str]) -> None:
    """Require a non-empty body inside the per-skill character cap."""
    text = body.strip()
    if not text:
        errors.append("playbook body must not be empty")
    elif len(text) > MAX_SKILL_CHARACTERS:
        errors.append(f"playbook body must be at most {MAX_SKILL_CHARACTERS} characters")


def _build_skill(values: dict[str, object], body: str) -> Skill:
    """Build a skill after validation has established every field's type."""
    tools = cast(list[object], values["tools"])
    return Skill(
        name=cast(str, values["name"]).strip(),
        description=cast(str, values["description"]).strip(),
        when=cast(str, values["when"]).strip(),
        tools=tuple(cast(str, tool) for tool in tools),
        body=body.strip(),
    )
