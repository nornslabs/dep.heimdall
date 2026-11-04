"""Register module jobs on the worker's Procrastinate application."""

from uuid import UUID

from procrastinate import App
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.adapters.github.actions import GitHubActionsResults
from heimdall.adapters.github.reader import GitHubApiReader
from heimdall.modules.intake.api import run_intake
from heimdall.modules.repro.api import RunOutcome, get_run, record_result, timeout_run
from heimdall.modules.tenancy.api import get_repo_full_name_in_transaction, get_tenant
from heimdall.modules.triage.api import get_pr_snapshot, handle_webhook_delivery
from heimdall.shared.context import TenantCtx, new_request_id
from heimdall.shared.github import RepoRef
from heimdall.shared.ids import InstallationId, RepoId


def register_intake_task(
    queue_app: App,
    sessions: async_sessionmaker[AsyncSession],
    reader: GitHubApiReader,
) -> None:
    """Register the head-scoped intake job with the worker.

    Args:
        queue_app: Procrastinate app used by the worker process.
        sessions: Session factory for job transactions.
        reader: Authenticated GitHub adapter.
    """

    @queue_app.task(name="intake.run_intake")
    async def run_intake_task(
        installation_id: int,
        repo_id: int,
        repo_full_name: str,
        pr_id: str,
        pr_number: int,
        head_sha: str,
        event_id: str,
    ) -> None:
        """Execute one intake job for a specific repository head."""
        if not repo_full_name:
            raise ValueError("intake job is missing repository name")
        tenant_id = InstallationId(installation_id)
        ctx = await get_tenant(sessions, tenant_id)
        repo = RepoRef(installation_id=tenant_id, full_name=repo_full_name)
        await run_intake(
            sessions,
            reader,
            ctx,
            repo,
            RepoId(repo_id),
            UUID(pr_id),
            pr_number,
            head_sha,
            event_id,
        )


def register_triage_task(
    queue_app: App,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Register the GitHub pull request event handler.

    Args:
        queue_app: Procrastinate app used by the worker process.
        sessions: Session factory for event handling.
    """

    @queue_app.task(name="triage.handle_event")
    async def handle_event_task(installation_id: int, delivery_id: str) -> None:
        """Load and apply one persisted GitHub delivery."""
        ctx = await get_tenant(sessions, InstallationId(installation_id))
        await handle_webhook_delivery(sessions, ctx, delivery_id)


def register_repro_timeout_task(
    queue_app: App,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Register scheduled reproduction deadlines with the worker.

    Args:
        queue_app: Procrastinate app used by the worker process.
        sessions: Session factory for timeout transactions.
    """

    @queue_app.task(name="repro.timeout")
    async def repro_timeout_task(installation_id: int, run_id: str) -> None:
        """Finish the reproduction step if its paired run has not returned."""
        ctx = await get_tenant(sessions, InstallationId(installation_id))
        await timeout_run(sessions, ctx, UUID(run_id))


def register_actions_result_task(
    queue_app: App,
    sessions: async_sessionmaker[AsyncSession],
    results: GitHubActionsResults,
) -> None:
    """Register authenticated GitHub Actions artifact ingestion.

    Args:
        queue_app: Procrastinate app used by the worker.
        sessions: Tenant-scoped database sessions.
        results: Adapter that downloads and validates workflow artifacts.
    """

    @queue_app.task(name="actions.process_workflow_run")
    async def process_workflow_run_task(
        installation_id: int,
        delivery_id: str,
        workflow_run_id: int,
        repo_full_name: str,
        workflow_path: str,
    ) -> None:
        """Save paired outcomes only for the matching tenant and repo.

        The run is identified by the artifact's run id. The workflow's own ``head_sha`` is the
        dispatched branch tip, not the PR head, so it says nothing about which run this is;
        ``record_result`` marks the result stale if the PR head moved since dispatch.
        """
        ctx = await get_tenant(sessions, InstallationId(installation_id))
        await handle_webhook_delivery(sessions, ctx, delivery_id)
        if workflow_path != ".github/workflows/heimdall-repro.yml":
            return
        repo = RepoRef(ctx.installation_id, repo_full_name)
        artifact = await results.get_paired_artifact(repo, workflow_run_id)
        run_id = UUID(artifact.run_id)
        run = await get_run(sessions, ctx, run_id)
        async with unit_of_work(sessions) as session:
            pr = await get_pr_snapshot(session, ctx, run.pr_id)
            if pr is None:
                return
            expected_repo = await get_repo_full_name_in_transaction(session, ctx, pr.repo_id)
            if expected_repo != repo_full_name:
                return
        await record_result(
            sessions,
            ctx,
            run_id,
            RunOutcome(
                passed=artifact.with_fix.passed,
                failures=artifact.with_fix.failures,
                duration_seconds=artifact.with_fix.duration_seconds,
            ),
            RunOutcome(
                passed=artifact.without_fix.passed,
                failures=artifact.without_fix.failures,
                duration_seconds=artifact.without_fix.duration_seconds,
            ),
        )


def register_installation_task(
    queue_app: App,
    sessions: async_sessionmaker[AsyncSession],
    default_room_id: str | None,
) -> None:
    """Register the installation lifecycle job.

    Args:
        queue_app: Procrastinate app used by the worker process.
        sessions: Session factory for event handling.
        default_room_id: Switch room given to repos the installation enables.
    """

    @queue_app.task(name="tenancy.on_installation_event")
    async def installation_event_task(installation_id: int, delivery_id: str) -> None:
        """Apply one persisted installation lifecycle delivery."""
        ctx = TenantCtx(
            installation_id=InstallationId(installation_id),
            request_id=new_request_id(),
        )
        await handle_webhook_delivery(sessions, ctx, delivery_id, default_room_id)
