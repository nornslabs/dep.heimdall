"""Route only authenticated, completed reproduction workflow events."""

import pytest

from heimdall.modules.triage._webhooks import _workflow_run_args


def test_workflow_run_job_uses_bounded_verified_metadata() -> None:
    """Keep the queued action job scoped to the workflow run and repository."""
    args = _workflow_run_args(
        {
            "workflow_run": {
                "id": 9182,
                "head_sha": "a" * 40,
                "workflow": {"path": ".github/workflows/heimdall-repro.yml@refs/heads/main"},
            },
            "repository": {"full_name": "octo/demo"},
        }
    )
    assert args == {
        "workflow_run_id": 9182,
        "head_sha": "a" * 40,
        "repo_full_name": "octo/demo",
        "workflow_path": ".github/workflows/heimdall-repro.yml",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"workflow_run": {}, "repository": {}},
        {
            "workflow_run": {
                "id": -1,
                "head_sha": "head",
                "workflow": {"path": ".github/workflows/heimdall-repro.yml"},
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
        _workflow_run_args(payload)
