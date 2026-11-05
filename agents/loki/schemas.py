"""Loki's submit schema, plus the check that every cited finding exists for this head.

The schema is what the model's ``submit_review`` call must satisfy; ``extra="forbid"`` and the
field limits reject anything else. Which ids were really saved is known only to the tools, so
``review_problems`` checks them after validation.
"""

from collections.abc import Callable, Sequence
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents._submit import SubmitTool
from agents.loki.config import MAX_FINDINGS

MAX_SUMMARY_CHARS = 400

SkipReason = Literal["docs_only", "no_findings"]


class LokiReview(BaseModel):
    """Loki's finished answer: the findings it saved, or why it saved none."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_ids: tuple[str, ...] = Field(default_factory=tuple[str, ...], max_length=MAX_FINDINGS)
    skipped_reason: SkipReason | None = None
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY_CHARS)

    @model_validator(mode="after")
    def _one_outcome(self) -> Self:
        """Require saved findings or a skip reason, never both and never neither."""
        if self.skipped_reason is not None and self.finding_ids:
            raise ValueError("skipped_reason and finding_ids cannot both be set")
        if self.skipped_reason is None and not self.finding_ids:
            raise ValueError("finding_ids or skipped_reason is required")
        return self

    def filtered(self, clean: Callable[[str], str]) -> Self:
        """Return a copy with the shared tone filter applied to the summary.

        Args:
            clean: The tone filter; returns an empty string when nothing about the code is left.

        Returns:
            A copy whose summary carries no comment about people.
        """
        return self.model_copy(update={"summary": clean(self.summary)})


SUBMIT_REVIEW = SubmitTool(
    "submit_review",
    LokiReview,
    "Finish with the finding ids save_findings accepted, or the reason there are none.",
)


def review_problems(review: LokiReview, saved_finding_ids: Sequence[str]) -> tuple[str, ...]:
    """List the cited finding ids this run did not save for the current head.

    Args:
        review: The submission that passed schema validation.
        saved_finding_ids: Ids ``save_findings`` accepted for this PR head during the run.

    Returns:
        One message per unknown id, empty when every id was saved. The caller hands these
        back to the model as its one repair round.
    """
    saved = set(saved_finding_ids)
    return tuple(
        f"finding_ids: {finding_id} was not saved through save_findings for this PR head"
        for finding_id in review.finding_ids
        if finding_id not in saved
    )
