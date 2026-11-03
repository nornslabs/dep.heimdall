"""The heimdall.yml schema: every section, its defaults, and the rules between fields."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Runner = Literal["pytest", "npm test", "go test"]
MechanicalCategory = Literal["lint", "typo", "rename", "missing_import", "docstring", "changelog"]

MAX_FINDINGS = 5
"""Loki's hard limit on findings per PR (AGENTS.md); a repo may lower it, never raise it."""

DEFAULT_CATEGORIES: tuple[MechanicalCategory, ...] = (
    "lint", "typo", "rename", "missing_import", "docstring", "changelog",
)  # fmt: skip


class _Section(BaseModel):
    """Frozen, and unknown keys are errors, so a typo in heimdall.yml is caught."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class IntakeConfig(_Section):
    """House rules the Gate checks before any agent looks at the PR."""

    require_linked_issue: bool = True
    require_assignee: bool = True
    required_labels: tuple[str, ...] = ()
    max_open_prs_per_author: int = Field(default=5, ge=1)
    require_cla: bool = False
    require_ci_green: bool = True


class ReproConfig(_Section):
    """How Huginn reproduces a fix on GitHub Actions."""

    test_cmd: str = "pytest -q"
    allowed_runners: tuple[Runner, ...] = ("pytest",)
    source_paths: tuple[str, ...] = ("src/",)
    workflow: str = "heimdall-repro.yml"

    @model_validator(mode="after")
    def _test_cmd_uses_an_allowed_runner(self) -> "ReproConfig":
        """Huginn may narrow the test command, never replace the runner (LLD)."""
        for runner in self.allowed_runners:
            if self.test_cmd == runner or self.test_cmd.startswith(f"{runner} "):
                return self
        allowed = ", ".join(repr(runner) for runner in self.allowed_runners)
        raise ValueError(f"test_cmd must start with an allowed runner: {allowed}")


class ReviewConfig(_Section):
    """Limits and skips for Loki's review."""

    max_findings: int = Field(default=MAX_FINDINGS, ge=1, le=MAX_FINDINGS)
    skip_paths: tuple[str, ...] = ("docs/**", "*.md")


class AutoSuggestConfig(_Section):
    """Mechanical fixes Heimdall may suggest under the auto_suggest policy."""

    enabled: bool = True
    categories: tuple[MechanicalCategory, ...] = DEFAULT_CATEGORIES


class RoomConfig(_Section):
    """Which Switch room this repo's PRs are triaged in."""

    switch_room: str = "triage"


class RepoConfig(_Section):
    """A repo's effective heimdall.yml after layering, validated.

    ``maintainers`` defaults to empty, so nobody can command the Desk until a repo names them.
    """

    version: Literal[1] = 1
    maintainers: tuple[str, ...] = ()
    intake: IntakeConfig = IntakeConfig()
    repro: ReproConfig = ReproConfig()
    review: ReviewConfig = ReviewConfig()
    auto_suggest: AutoSuggestConfig = AutoSuggestConfig()
    room: RoomConfig = RoomConfig()
