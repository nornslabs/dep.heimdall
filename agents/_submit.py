"""The ``submit_*`` local tool: the only way an agent run ends with a result.

Its arguments must validate against the agent's pydantic schema. Errors go back to the model
by field path and message only, never echoing the submitted values.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from heimdall.adapters.ai import ToolSpec

SUBMIT_PREFIX = "submit_"

type SubmissionCheck[T] = Callable[[T], Awaitable[tuple[str, ...]]]
"""Checks a submission must still pass after its schema; returns one line per problem."""


@dataclass(frozen=True)
class InvalidSubmission:
    """A submission that failed validation, with the errors to show the model."""

    errors: str


class SubmitTool[T: BaseModel]:
    """A ``submit_*`` tool whose arguments must be a valid ``T``."""

    def __init__(
        self,
        name: str,
        model: type[T],
        description: str,
        *,
        cross_check: SubmissionCheck[T] | None = None,
    ) -> None:
        """Bind the tool name to the schema a finished answer must match.

        Args:
            name: Tool name the model calls; must start with ``submit_``.
            model: Pydantic model of a finished answer.
            description: What the model should submit, shown in the tool list.
            cross_check: Optional check against data only tools can see, run after the schema;
                its problems go back to the model as the same one repair round.

        Raises:
            ValueError: ``name`` does not start with ``submit_``.
        """
        if not name.startswith(SUBMIT_PREFIX):
            raise ValueError(f"submit tool name must start with {SUBMIT_PREFIX!r}")
        self._model = model
        self._cross_check = cross_check
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

    async def verify(self, payload: T) -> InvalidSubmission | None:
        """Check a schema-valid answer against the data its tools returned.

        Args:
            payload: The answer that passed ``check``.

        Returns:
            The problems as a repair message, or None when the answer stands. The text names
            what disagrees, never the submitted values.
        """
        if self._cross_check is None:
            return None
        problems = await self._cross_check(payload)
        if not problems:
            return None
        lines = [f"error: {self.name} does not match what the tools returned; fix these:"]
        lines.extend(f"- {problem}" for problem in problems)
        return InvalidSubmission("\n".join(lines))

    def _describe(self, error: ValidationError) -> str:
        """List each validation error by field path, leaving out the submitted values."""
        lines = [f"error: {self.name} arguments are invalid; fix these and call it again:"]
        for detail in error.errors(include_input=False, include_url=False):
            path = ".".join(str(part) for part in detail["loc"]) or "(root)"
            lines.append(f"- {path}: {detail['msg']}")
        return "\n".join(lines)
