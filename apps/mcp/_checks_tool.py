"""Heimdall's rerun tool: ask triage to run all of a PR's checks again.

The tool changes no PR state itself. It asks triage, whose state machine decides whether the
PR may go back to intake (the repro and review follow once intake passes), and reports the
answer so the agent can say what happened.
"""

from uuid import UUID

from mcp.server.mcpserver import MCPServer
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.unit_of_work import unit_of_work
from heimdall.modules.triage.api import Rejected, Transition, get_pr_snapshot, request_recheck
from heimdall.shared.context import TenantCtx, new_request_id
from heimdall.shared.ids import InstallationId

RERUN_CHECKS_TOOL = "rerun_checks"


def register_checks_tool(
    server: MCPServer, sessions: async_sessionmaker[AsyncSession]
) -> frozenset[str]:
    """Add ``rerun_checks`` to a server.

    Args:
        server: The agent-scoped MCP server.
        sessions: Tenant database session factory.

    Returns:
        The names of the tools registered.
    """

    @server.tool(
        name=RERUN_CHECKS_TOOL,
        description=(
            "Run all of this pull request's checks again on its current head: intake first, "
            "then the tests and the review if intake passes. Use it whenever the maintainer "
            "wants the PR looked at or tested again, in any wording. Look at the PR's state "
            "first; the result says what it was. Refused for a closed PR, one with a draft "
            "awaiting approval, or one already being checked."
        ),
        structured_output=True,
    )
    async def rerun_checks(installation_id: int, pr_id: str, repo_full_name: str) -> dict[str, str]:
        """Ask triage to move the PR back to intake and report whether it did."""
        ctx = TenantCtx(
            installation_id=InstallationId(installation_id), request_id=new_request_id()
        )
        pr_uuid = UUID(pr_id)
        was = await _state_name(sessions, ctx, pr_uuid)
        outcome = await request_recheck(sessions, ctx, pr_uuid, repo_full_name)
        if isinstance(outcome, Rejected):
            return {"rechecked": "no", "reason": outcome.reason.value, "was": was}
        return _accepted(outcome, was)

    return frozenset({RERUN_CHECKS_TOOL})


async def _state_name(
    sessions: async_sessionmaker[AsyncSession], ctx: TenantCtx, pr_id: UUID
) -> str:
    """Name the PR's state now, so the reply can say what the rerun changed."""
    async with unit_of_work(sessions) as session:
        snapshot = await get_pr_snapshot(session, ctx, pr_id)
    return snapshot.state.value if snapshot is not None else "unknown"


def _accepted(transition: Transition, was: str) -> dict[str, str]:
    """Describe a rerun triage accepted."""
    return {"rechecked": "yes", "was": was, "state": transition.to.value}
