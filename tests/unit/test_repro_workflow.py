"""The reproduction workflow must have the trusted runner on disk before it copies it."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "heimdall-repro.yml"
RUNNER_SCRIPT = "run-repro.py"
CHECKOUT_ACTION = "actions/checkout"


def _steps() -> list[dict[str, object]]:
    """Return the steps of the workflow's single job, in order."""
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps: list[dict[str, object]] = document["jobs"]["reproduce"]["steps"]
    return steps


def _is_checkout(step: dict[str, object]) -> bool:
    """Tell whether a step runs the checkout action."""
    return str(step.get("uses", "")).startswith(CHECKOUT_ACTION)


def _pins_a_commit(step: dict[str, object]) -> bool:
    """Tell whether a checkout step names a ref instead of taking the default branch."""
    options = step.get("with")
    return isinstance(options, dict) and "ref" in options


def _copy_index(steps: list[dict[str, object]]) -> int:
    """Return the position of the step that copies the runner script out of the workspace."""
    for index, step in enumerate(steps):
        command = step.get("run")
        if isinstance(command, str) and command.startswith("cp ") and RUNNER_SCRIPT in command:
            return index
    raise AssertionError("the workflow has no step that copies the runner script")


def test_default_branch_is_checked_out_before_the_runner_is_copied() -> None:
    steps = _steps()

    earlier = steps[: _copy_index(steps)]

    assert any(_is_checkout(step) and not _pins_a_commit(step) for step in earlier), (
        "the runner is copied before any checkout, so the workspace is empty and cp fails"
    )


def test_pull_request_commits_are_checked_out_after_the_runner_is_copied() -> None:
    steps = _steps()

    copy_index = _copy_index(steps)
    pinned = [
        index for index, step in enumerate(steps) if _is_checkout(step) and _pins_a_commit(step)
    ]

    assert pinned, "the workflow must check out the head and base commits"
    assert all(index > copy_index for index in pinned), (
        "pull request code must not be checked out before the trusted runner is saved"
    )
