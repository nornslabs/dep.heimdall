"""Private parsing for Loki: model reply text into validated finding objects."""

import json
from typing import cast

FENCE = "```"


def parse_findings(response: str, limit: int) -> list[dict[str, str | int | None]]:
    """Accept only structurally valid finding objects, keeping at most ``limit``.

    Args:
        response: Model reply text: a JSON object, optionally inside a code fence.
        limit: Most findings to keep (MAX_FINDINGS).

    Returns:
        Findings with ``file``, ``line``, ``text``, ``severity`` and ``mechanical_category``.

    Raises:
        ValueError: the reply is not JSON, has no findings array, or holds a non-object.
    """
    try:
        value: object = json.loads(_strip_fence(response))
    except json.JSONDecodeError as error:
        raise ValueError("model response was not valid JSON") from error
    response_object = json_object(value)
    raw_findings = response_object.get("findings") if response_object is not None else None
    if not isinstance(raw_findings, list):
        raise ValueError("model response must contain a findings array")
    findings: list[dict[str, str | int | None]] = []
    for raw_item in cast(list[object], raw_findings):
        finding = _finding(raw_item)
        if finding is not None:
            findings.append(finding)
    return findings[:limit]


def _strip_fence(response: str) -> str:
    """Drop a surrounding Markdown code fence, which many models add around JSON."""
    text = response.strip()
    if not text.startswith(FENCE) or not text.endswith(FENCE):
        return text
    body = text[len(FENCE) : -len(FENCE)]
    first_newline = body.find("\n")
    return body[first_newline + 1 :] if first_newline != -1 else body


def _finding(raw_item: object) -> dict[str, str | int | None] | None:
    """Keep one well-typed finding; skip items with missing or wrongly typed fields."""
    item = json_object(raw_item)
    if item is None:
        raise ValueError("model finding must be an object")
    file = item.get("file")
    line = item.get("line")
    text = item.get("text")
    severity = item.get("severity")
    category = item.get("mechanical_category")
    if not isinstance(file, str) or isinstance(line, bool) or not isinstance(line, int):
        return None
    if not isinstance(text, str) or not isinstance(severity, str):
        return None
    if category is not None and not isinstance(category, str):
        return None
    return {
        "file": file,
        "line": line,
        "text": text,
        "severity": severity,
        "mechanical_category": category,
    }


def json_object(value: object) -> dict[str, object] | None:
    """Return a decoded JSON object with string keys, or None for any other value."""
    if not isinstance(value, dict):
        return None
    return {str(key): item for key, item in cast(dict[object, object], value).items()}


def json_objects(values: list[object]) -> tuple[dict[str, object], ...]:
    """Keep only the JSON objects from a decoded list, in order."""
    objects: list[dict[str, object]] = []
    for value in values:
        decoded = json_object(value)
        if decoded is not None:
            objects.append(decoded)
    return tuple(objects)
