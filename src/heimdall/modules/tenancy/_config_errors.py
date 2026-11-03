"""Turns validation failures into the exact, readable lines shown to a repo's maintainers."""

import difflib
from collections.abc import Iterable

from pydantic import BaseModel, ValidationError
from pydantic_core import ErrorDetails

from heimdall.shared.errors import ConfigInvalid

TOP_LEVEL = "(top level)"
SIMPLE_MESSAGES = {
    "bool_type": "must be true or false",
    "bool_parsing": "must be true or false",
    "int_type": "must be a whole number",
    "int_parsing": "must be a whole number",
    "int_from_float": "must be a whole number",
    "string_type": "must be text",
    "tuple_type": "must be a list",
    "model_type": "must be a mapping of keys to values",
    "model_attributes_type": "must be a mapping of keys to values",
}


def config_invalid(source: str, problems: Iterable[str]) -> ConfigInvalid:
    """Build the error for one file (or the merged config) listing every problem.

    Args:
        source: The file name, or a label for the merged config.
        problems: One ``path: problem`` string per problem.

    Returns:
        A ConfigInvalid whose message is ``"<source> is invalid:"`` plus one line per problem.
    """
    lines = "\n".join(f"- {problem}" for problem in problems)
    return ConfigInvalid(f"{source} is invalid:\n{lines}")


def describe_errors(error: ValidationError, model: type[BaseModel]) -> list[str]:
    """Describe each pydantic error as ``dotted.path: problem`` in the product's words.

    Args:
        error: The validation failure.
        model: The model that was validated, used to suggest a key for typos.

    Returns:
        One line per error, in pydantic's order.
    """
    return [_describe(detail, model) for detail in error.errors()]


def _describe(detail: ErrorDetails, model: type[BaseModel]) -> str:
    path = ".".join(str(part) for part in detail["loc"]) or TOP_LEVEL
    return f"{path}: {_problem(detail, model)}"


def _problem(detail: ErrorDetails, model: type[BaseModel]) -> str:
    """The human sentence for one pydantic error type, with context where it helps."""
    kind = detail["type"]
    context = detail.get("ctx") or {}
    if kind == "extra_forbidden":
        return _unknown_key(detail["loc"], model)
    if kind == "greater_than_equal":
        return f"must be at least {context['ge']}"
    if kind == "less_than_equal":
        return f"must be at most {context['le']}"
    if kind == "literal_error":
        return f"must be one of {context['expected']}"
    if kind == "value_error":
        return str(context["error"])
    return SIMPLE_MESSAGES.get(kind, detail["msg"])


def _unknown_key(loc: tuple[int | str, ...], model: type[BaseModel]) -> str:
    suggestion = _closest_key(loc, model)
    if suggestion is None:
        return "unknown key"
    return f"unknown key (did you mean {suggestion!r}?)"


def _closest_key(loc: tuple[int | str, ...], model: type[BaseModel]) -> str | None:
    """Find the real key nearest to a misspelt one, walking the model along ``loc``."""
    section: type[BaseModel] = model
    for part in loc[:-1]:
        field = section.model_fields.get(str(part))
        annotation = field.annotation if field is not None else None
        if not (isinstance(annotation, type) and issubclass(annotation, BaseModel)):
            return None
        section = annotation
    matches = difflib.get_close_matches(str(loc[-1]), list(section.model_fields), n=1)
    return matches[0] if matches else None
