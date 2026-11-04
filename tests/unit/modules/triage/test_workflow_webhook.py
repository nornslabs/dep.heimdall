"""Route only authenticated, completed reproduction workflow events."""

import pytest

from heimdall.modules.triage._webhooks import workflow_run_args

# GitHub sends the workflow file on ``workflow_run.path`` and on a top-level ``workflow``
# object; ``workflow_run`` itself has no nested ``workflow``.
REPRO_PATH = ".github/workflows/heimdall-repro.yml"


def test_workflow_run_job_uses_bounded_verified_metadata() -> None:
    """Keep the queued action job scoped to the workflow run and repository."""
    args = workflow_run_args(
        {
            "workflow": {"name": "Heimdall reproduction", "path": REPRO_PATH},
            "workflow_run": {"id": 9182, "head_sha": "a" * 40, "path": REPRO_PATH},
            "repository": {"full_name": "octo/demo"},
        }
    )
    assert args == {
        "workflow_run_id": 9182,
        "repo_full_name": "octo/demo",
        "workflow_path": REPRO_PATH,
    }


def test_workflow_path_drops_the_ref_suffix() -> None:
    """A run path can carry ``@ref``; routing compares the bare workflow file."""
    args = workflow_run_args(
        {
            "workflow_run": {"id": 9182, "head_sha": "a" * 40, "path": f"{REPRO_PATH}@main"},
            "repository": {"full_name": "octo/demo"},
        }
    )
    assert args["workflow_path"] == REPRO_PATH


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"workflow_run": {}, "repository": {}},
        {
            "workflow_run": {
                "id": -1,
                "head_sha": "head",
                "path": REPRO_PATH,
            },
            "repository": {"full_name": "octo/demo"},
        },
    ],
)
def test_workflow_run_job_rejects_missing_or_invalid_metadata(
    payload: dict[str, object],
) -> None:
    """Do not queue unscoped workflow result processing."""
    with pytest.raises(ValueError):
        workflow_run_args(payload)
