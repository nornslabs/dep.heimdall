"""Heimdall's submit schema, plus the check that the id it names was saved by this run.

The schema is what the model's ``submit_result`` call must satisfy; ``extra="forbid"``, the
action literals and the matching field rule reject anything else. Whether the id is real is
known only to the tools, so ``desk_result_problems`` checks it after validation.
"""

from collections.abc import Callable, Sequence
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents._submit import SubmitTool

MAX_EXPLANATION_CHARS = 800

DeskAction = Literal["card", "draft", "mechanical_task", "explanation"]

FIELD_FOR_ACTION: dict[str, str] = {
    "card": "card_id",
    "draft": "draft_id",
    "mechanical_task": "task_id",
    "explanation": "explanation",
}
"""The one field each action must carry, and the only field it may carry."""


class DeskResult(BaseModel):
    """Heimdall's finished answer: which artifact it saved, or what it explained."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: DeskAction
    card_id: str | None = None
    draft_id: str | None = None
    task_id: str | None = None
    explanation: str | None = Field(default=None, max_length=MAX_EXPLANATION_CHARS)

    @model_validator(mode="after")
    def _matching_field_only(self) -> Self:
        """Require exactly the field the action names, filled in, and no other field."""
        named = {
            "card_id": self.card_id,
            "draft_id": self.draft_id,
            "task_id": self.task_id,
            "explanation": self.explanation,
        }
        expected = FIELD_FOR_ACTION[self.action]
        unexpected = [
            name for name, value in named.items() if name != expected and value is not None
        ]
        if unexpected:
            raise ValueError(
                f"{', '.join(unexpected)} must not be set when action is {self.action!r}"
            )
        chosen = named[expected]
        if chosen is None or not chosen.strip():
            raise ValueError(f"{expected} is required when action is {self.action!r}")
        return self

    def filtered(self, clean: Callable[[str], str]) -> Self:
        """Return a copy with the shared tone filter applied to the explanation.

        Args:
            clean: The tone filter; returns an empty string when nothing about the code is left.

        Returns:
            A copy without any comment about people. An explanation the filter empties stays
            empty, and the renderer says so rather than posting a stray prefix.
        """
        if self.explanation is None:
            return self
        return self.model_copy(update={"explanation": clean(self.explanation)})


SUBMIT_RESULT = SubmitTool(
    "submit_result",
    DeskResult,
    "Finish by naming the card, draft or task you saved, or with your explanation.",
)


def desk_result_problems(result: DeskResult, saved_ids: Sequence[str]) -> tuple[str, ...]:
    """List what a validated result claims that this run did not save.

    Args:
        result: The submission that passed schema validation.
        saved_ids: Ids Heimdall's tenant-scoped tools returned for this PR head during the run.

    Returns:
        One message per id this run did not save, empty when the result names saved work or is
        an explanation. The caller hands these back to the model as its one repair round.
    """
    if result.action == "explanation":
        return ()
    named = {
        "card": result.card_id,
        "draft": result.draft_id,
        "mechanical_task": result.task_id,
    }.get(str(result.action))
    if named in set(saved_ids):
        return ()
    return (f"{FIELD_FOR_ACTION[result.action]}: {named} was not saved for this PR head",)
