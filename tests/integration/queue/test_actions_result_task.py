"""The worker job that turns a finished repro workflow into a recorded result."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from apps.worker.tasks import register_actions_result_task
from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.adapters.github.actions import ArtifactOutcome, GitHubActionsResults, PairedArtifact
from heimdall.adapters.queue.app import create_queue_app
from heimdall.adapters.queue.enqueue import DEFAULT_QUEUE
from heimdall.modules.repro.api import ReproStatus, get_run, start_run
from heimdall.modules.tenancy.api import on_installation_event
from heimdall.modules.triage.api import (
    EventKind,
    Facts,
    PrEvent,
    handle_event,
    on_step_done,
    receive_webhook,
)
from heimdall.modules.triage.models import Pr
from heimdall.shared.context import TenantCtx
from heimdall.shared.github import RepoRef
from heimdall.shared.ids import InstallationId, RepoId
from tests.integration.db.installation_helpers import created_installation

pytestmark = pytest.mark.integration

REPO = "actions-test/demo"
PR_HEAD = "1" * 40
BASE_TIP = "2" * 40
REPRO_PATH = ".github/workflows/heimdall-repro.yml"
WORKFLOW_RUN_ID = 7001


class _ScriptedResults(GitHubActionsResults):
    """Hands back one artifact instead of downloading it from GitHub."""

    def __init__(self, artifact: PairedArtifact) -> None:
        """Keep the artifact every lookup returns."""
        self._artifact = artifact

    async def get_paired_artifact(self, repo: RepoRef, workflow_run_id: int) -> PairedArtifact:
        """Return the scripted artifact."""
        return self._artifact


async def _dispatched_run(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[TenantCtx, uuid.UUID]:
    """Create a PR in ``checking`` with one dispatched repro attempt for its head."""
    installation_id = int(uuid.uuid4().int % 1_000_000_000) + 2_000_000
    repo_id = RepoId(installation_id + 1)
    ctx = TenantCtx(installation_id=InstallationId(installation_id), request_id="actions-test")
    installation = created_installation(installation_id, "actions-test", repo_id, REPO)
    await on_installation_event(sessions, installation)
    opened = PrEvent(
        event_id="open-actions",
        repo_id=repo_id,
        repo_full_name=REPO,
        number=1,
        author="octocat",
        head_sha=PR_HEAD,
        base_sha=BASE_TIP,
        base_ref="main",
        kind=EventKind.PR_OPENED,
        facts=Facts(),
    )
    await handle_event(sessions, ctx, opened)
    async with unit_of_work(sessions) as session:
        pr_id = (await session.exec(select_for_tenant(Pr, ctx.installation_id))).one().id
    intake = Facts(intake_passed=True)
    await on_step_done(sessions, ctx, pr_id, "intake", EventKind.INTAKE_DONE, PR_HEAD, intake)
    run = await start_run(sessions, ctx, pr_id, PR_HEAD, "pytest -q")
    return ctx, run.id


def _completed_dispatch_webhook(installation_id: int) -> dict[str, object]:
    """A ``workflow_run.completed`` body for a dispatched run, in GitHub's shape.

    A ``workflow_dispatch`` run is checked out at the dispatched branch, so GitHub reports
    that branch's tip as ``head_sha``. The PR head only reaches the run as a workflow input.
    """
    return {
        "installation": {"id": installation_id},
        "repository": {"full_name": REPO},
        "workflow": {"path": REPRO_PATH},
        "workflow_run": {
            "id": WORKFLOW_RUN_ID,
            "event": "workflow_dispatch",
            "head_sha": BASE_TIP,
            "path": REPRO_PATH,
        },
    }


async def test_dispatched_repro_result_is_recorded_though_run_reports_base_tip(
    database_url: str, db_schema: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The run is matched by the artifact's run id, not by the workflow's checkout SHA."""
    ctx, run_id = await _dispatched_run(sessions)
    passed = ArtifactOutcome(passed=True, failures=(), duration_seconds=1.0)
    failed = ArtifactOutcome(passed=False, failures=("test_bug",), duration_seconds=1.0)
    queue_app = create_queue_app(database_url, schema=db_schema)
    register_actions_result_task(
        queue_app, sessions, _ScriptedResults(PairedArtifact(str(run_id), passed, failed))
    )
    delivery_id = f"actions-{uuid.uuid4().hex}"
    payload = _completed_dispatch_webhook(ctx.installation_id)

    await receive_webhook(sessions, delivery_id, "workflow_run", "completed", payload)
    async with queue_app.open_async():
        await queue_app.run_worker_async(queues=[DEFAULT_QUEUE], wait=False)

    run = await get_run(sessions, ctx, run_id)
    assert run.status is ReproStatus.FINISHED
