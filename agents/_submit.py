"""The ``submit_*`` local tool: the only way an agent run ends with a result.

Its arguments must validate against the agent's pydantic schema. Errors go back to the model
by field path and message only, never echoing the submitted values.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from heimdall.adapters.ai import ToolSpec

SUBMIT_PREFIX = "submit_"


@dataclass(frozen=True)
class InvalidSubmission:
    """A submission that failed validation, with the errors to show the model."""

    errors: str


class SubmitTool[T: BaseModel]:
    """A ``submit_*`` tool whose arguments must be a valid ``T``."""

    def __init__(self, name: str, model: type[T], description: str) -> None:
        """Bind the tool name to the schema a finished answer must match.

        Args:
            name: Tool name the model calls; must start with ``submit_``.
            model: Pydantic model of a finished answer.
            description: What the model should submit, shown in the tool list.

        Raises:
            ValueError: ``name`` does not start with ``submit_``.
        """
        if not name.startswith(SUBMIT_PREFIX):
            raise ValueError(f"submit tool name must start with {SUBMIT_PREFIX!r}")
        self._model = model
        self.spec = ToolSpec(name, description, model.model_json_schema())

    @property
    def name(self) -> str:
        """The tool name the model calls to finish."""
        return self.spec.name

    def check(self, arguments: Mapping[str, object]) -> T | InvalidSubmission:
        """Parse a submission, or describe exactly what is wrong with it.

        Args:
            arguments: Parsed arguments from the model's ``submit_*`` call.

        Returns:
            The validated answer, or the errors as ``- field: message`` lines.
        """
        try:
            return self._model.model_validate(dict(arguments))
        except ValidationError as error:
            return InvalidSubmission(self._describe(error))

    def _describe(self, error: ValidationError) -> str:
        """List each validation error by field path, leaving out the submitted values."""
        lines = [f"error: {self.name} arguments are invalid; fix these and call it again:"]
        for detail in error.errors(include_input=False, include_url=False):
            path = ".".join(str(part) for part in detail["loc"]) or "(root)"
            lines.append(f"- {path}: {detail['msg']}")
        return "\n".join(lines)
