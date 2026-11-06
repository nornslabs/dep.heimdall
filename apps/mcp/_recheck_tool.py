"""Heimdall's recheck tool: ask triage to run a PR's intake again.

The tool changes no PR state itself. It asks triage, whose state machine decides whether the
PR may go back to intake, and reports the answer so the agent can say what happened.
"""

from uuid import UUID

from mcp.server.mcpserver import MCPServer
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.modules.triage.api import Rejected, Transition, request_recheck
from heimdall.shared.context import TenantCtx, new_request_id
from heimdall.shared.ids import InstallationId

RECHECK_TOOL = "recheck_intake"


def register_recheck_tool(
    server: MCPServer, sessions: async_sessionmaker[AsyncSession]
) -> frozenset[str]:
    """Add ``recheck_intake`` to a server.

    Args:
        server: The agent-scoped MCP server.
        sessions: Tenant database session factory.

    Returns:
        The names of the tools registered.
    """

    @server.tool(
        name=RECHECK_TOOL,
        description=(
            "Run this pull request's intake checks again on its current head, then the repro "
            "and review follow if intake passes. Use when a maintainer asks to recheck or "
            "rerun. Refused for a closed PR or one with a draft awaiting approval."
        ),
        structured_output=True,
    )
    async def recheck_intake(
        installation_id: int, pr_id: str, repo_full_name: str
    ) -> dict[str, str]:
        """Ask triage to move the PR back to intake and report whether it did."""
        ctx = TenantCtx(
            installation_id=InstallationId(installation_id), request_id=new_request_id()
        )
        outcome = await request_recheck(sessions, ctx, UUID(pr_id), repo_full_name)
        if isinstance(outcome, Rejected):
            return {"rechecked": "no", "reason": outcome.reason.value}
        return _accepted(outcome)

    return frozenset({RECHECK_TOOL})


def _accepted(transition: Transition) -> dict[str, str]:
    """Describe a recheck triage accepted."""
    return {"rechecked": "yes", "state": transition.to.value}
