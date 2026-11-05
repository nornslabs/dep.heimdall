"""Huginn's submit schema, plus the run cross-check a validated verdict must still pass.

The schema is what the model's ``submit_verdict`` call must satisfy; ``extra="forbid"`` and the
field limits reject anything else. It cannot see saved runs, so the counts and ids it carries
are checked against ``get_repro`` afterwards by ``verdict_problems``.
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Literal, Protocol, Self, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents._submit import SubmitTool

MAX_REPRO_IDS = 3
"""Most run ids one verdict may cite; the same bound as ``MAX_REPRO_RUNS``."""

MAX_FAILING_ASSERTION_CHARS = 300
MAX_SUMMARY_CHARS = 400

VerdictKind = Literal["reproduced", "not_covered", "setup_failed", "inconclusive"]


class ModeResult(BaseModel):
    """How the runs a verdict names ended in one mode: with the fix, or without it.

    Each counter is over runs, not tests: ``passed`` runs passed the configured command,
    ``failed`` runs ran it and it failed, ``errors`` runs left no saved outcome.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    errors: int = Field(ge=0)


class HuginnVerdict(BaseModel):
    """Huginn's finished answer: the verdict a bounded reproduction run reaches."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: VerdictKind
    test_cmd: str
    repro_ids: tuple[str, ...] = Field(default_factory=tuple[str, ...], max_length=MAX_REPRO_IDS)
    with_fix: ModeResult
    without_fix: ModeResult
    failing_assertion: str = Field(default="", max_length=MAX_FAILING_ASSERTION_CHARS)
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY_CHARS)

    @model_validator(mode="after")
    def _counts_cover_the_named_runs(self) -> Self:
        """Require each mode's counters to account for every run the verdict names."""
        named_runs = len(self.repro_ids)
        for name, mode in (("with_fix", self.with_fix), ("without_fix", self.without_fix)):
            if mode.passed + mode.failed + mode.errors != named_runs:
                raise ValueError(
                    f"{name}: passed + failed + errors must equal the number of repro_ids"
                )
        return self

    def filtered(self, clean: Callable[[str], str]) -> Self:
        """Return a copy with the shared tone filter applied to the prose fields.

        Args:
            clean: The tone filter; returns an empty string when nothing about the code is left.

        Returns:
            A copy whose summary and failing assertion carry no comment about people.
        """
        return self.model_copy(
            update={
                "failing_assertion": clean(self.failing_assertion),
                "summary": clean(self.summary),
            }
        )


SUBMIT_VERDICT = SubmitTool(
    "submit_verdict",
    HuginnVerdict,
    "Finish with the verdict, the run ids it rests on and the paired run counts.",
)


class ReproRunReader(Protocol):
    """What the cross-check needs from Huginn's tools: one saved run per id, tenant-scoped."""

    async def get_repro(self, installation_id: int, run_id: str) -> dict[str, object]:
        """Read one saved run and its paired test outcomes."""
        ...


async def verdict_problems(
    tools: ReproRunReader,
    installation_id: int,
    verdict: HuginnVerdict,
    head_sha: str,
) -> tuple[str, ...]:
    """List where a validated verdict disagrees with the runs ``get_repro`` returned.

    Args:
        tools: Huginn's MCP client, whose ``get_repro`` is already tenant-scoped.
        installation_id: Tenant owning the pull request.
        verdict: The submission that passed schema validation.
        head_sha: PR head the run is about; a run saved for another head is refused.

    Returns:
        One message per problem, empty when every id exists for this head and both counter
        sets match. The caller hands these back to the model as its one repair round.
    """
    problems: list[str] = []
    runs: list[dict[str, object]] = []
    for run_id in verdict.repro_ids:
        run = await tools.get_repro(installation_id, run_id)
        if str(run.get("head_sha")) != head_sha:
            problems.append(f"repro_ids: {run_id} is not a run for this PR head")
            continue
        runs.append(run)
    if problems:
        return tuple(problems)
    problems.extend(_count_problems("with_fix", verdict.with_fix, _count(runs, "with_fix")))
    problems.extend(
        _count_problems("without_fix", verdict.without_fix, _count(runs, "without_fix"))
    )
    return tuple(problems)


def _count(runs: Sequence[Mapping[str, object]], mode: str) -> ModeResult:
    """Count one mode's saved outcomes the way ``ModeResult`` counts them."""
    passed, failed, errors = 0, 0, 0
    for run in runs:
        outcome = run.get(mode)
        if not isinstance(outcome, dict):
            errors += 1
        elif cast(dict[str, object], outcome).get("passed") is True:
            passed += 1
        else:
            failed += 1
    return ModeResult(passed=passed, failed=failed, errors=errors)


def _count_problems(name: str, submitted: ModeResult, actual: ModeResult) -> tuple[str, ...]:
    """Describe one mode's counter mismatch, or say nothing when they agree."""
    if submitted == actual:
        return ()
    return (f"{name}: submitted {_counts(submitted)} but get_repro reported {_counts(actual)}",)


def _counts(mode: ModeResult) -> str:
    """Format counters as ``passed/failed/errors`` for a repair message."""
    return f"{mode.passed}/{mode.failed}/{mode.errors}"
