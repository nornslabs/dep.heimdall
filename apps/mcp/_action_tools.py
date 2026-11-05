"""Heimdall's pull-request action drafts: close, reopen, merge and ask the contributor.

Each tool only saves a draft of a fixed kind. Nothing here touches GitHub: the kind is part of
what a maintainer approves, and the GitHub sender acts on the approved draft's own kind, so the
model can choose neither the action nor the text after approval.
"""

from uuid import UUID

from mcp.server.mcpserver import MCPServer
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.modules.desk.api import (
    PR_ASK_KIND,
    PR_CLOSE_KIND,
    PR_MERGE_KIND,
    PR_REOPEN_KIND,
    save_draft,
)
from heimdall.shared.context import TenantCtx, new_request_id
from heimdall.shared.ids import InstallationId

ACTION_DRAFT_TOOLS = {
    "draft_close": (PR_CLOSE_KIND, "Draft the comment that closes this pull request."),
    "draft_reopen": (PR_REOPEN_KIND, "Draft the comment that reopens this pull request."),
    "draft_merge": (PR_MERGE_KIND, "Draft the commit message that merges this pull request."),
    "draft_ask_contributor": (
        PR_ASK_KIND,
        "Draft a comment asking the contributor to add or change something.",
    ),
}
"""Tool name to the draft kind it saves and its description."""


def register_action_draft_tools(
    server: MCPServer, sessions: async_sessionmaker[AsyncSession]
) -> frozenset[str]:
    """Add the four action-draft tools to a server, each bound to its own draft kind.

    Args:
        server: The agent-scoped MCP server.
        sessions: Tenant database session factory.

    Returns:
        The names of the tools registered.
    """
    for name, (kind, description) in ACTION_DRAFT_TOOLS.items():
        _register(server, sessions, name, kind, description)
    return frozenset(ACTION_DRAFT_TOOLS)


def _register(
    server: MCPServer,
    sessions: async_sessionmaker[AsyncSession],
    name: str,
    kind: str,
    description: str,
) -> None:
    """Register one tool that saves a draft of ``kind``."""

    @server.tool(name=name, description=description, structured_output=True)
    async def draft_action(
        installation_id: int, pr_id: str, head_sha: str, body: str
    ) -> dict[str, str]:
        """Persist an approval-bound draft of this tool's kind without acting on GitHub."""
        ctx = TenantCtx(
            installation_id=InstallationId(installation_id), request_id=new_request_id()
        )
        draft = await save_draft(sessions, ctx, UUID(pr_id), head_sha, kind, body)
        return {"draft_id": str(draft.id), "body_hash": draft.body_hash}
