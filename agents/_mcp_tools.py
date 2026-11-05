"""In-process MCP clients for the agents' scoped tool servers.

Going through ``apps.mcp.main.create_server`` keeps the per-agent tool allowlist and the
server's argument validation in the path, the same way a stdio MCP session would.
"""

from collections.abc import Mapping
from typing import cast

from mcp.server.mcpserver import MCPServer

from agents._toolbox import AgentTool, ToolHandler
from heimdall.adapters.ai import ToolSpec


def as_object(value: object) -> dict[str, object]:
    """Narrow a parsed JSON value to an object, failing loudly on a bad tool result."""
    if not isinstance(value, dict):
        raise TypeError("MCP tool result must be a JSON object")
    items: dict[object, object] = value  # pyright: ignore[reportUnknownVariableType]
    return {str(key): item for key, item in items.items()}


async def mcp_agent_tools(server: MCPServer, names: frozenset[str]) -> tuple[AgentTool, ...]:
    """Wrap an agent server's allowlisted tools as tools the runtime can offer the model.

    Args:
        server: The agent-scoped server built by ``apps.mcp.main.create_server``.
        names: Tools the agent may call; every one must be registered on ``server``.

    Returns:
        One runtime tool per name, sorted by name.

    Raises:
        ValueError: The server does not register one of the named tools.
    """
    client = McpToolClient(server)
    listed = {tool.name: tool for tool in await server.list_tools()}
    missing = sorted(names - listed.keys())
    if missing:
        raise ValueError(f"MCP server is missing tools: {missing}")
    return tuple(
        AgentTool(
            ToolSpec(tool.name, tool.description or tool.name, tool.input_schema),
            _handler(client, tool.name),
        )
        for tool in (listed[name] for name in sorted(names))
    )


def _handler(client: "McpToolClient", name: str) -> ToolHandler:
    """Return a handler that calls one MCP tool with the model's arguments."""

    async def handler(arguments: Mapping[str, object]) -> object:
        return await client.call(name, **dict(arguments))

    return handler


class McpToolClient:
    """Calls one agent's MCP tools and returns their structured results."""

    def __init__(self, server: MCPServer) -> None:
        """Bind the agent-scoped server built by ``apps.mcp.main.create_server``."""
        self._server = server

    async def call(self, name: str, **arguments: object) -> dict[str, object]:
        """Run one tool and return its structured output.

        Raises:
            RuntimeError: the tool reported an error instead of a result.
        """
        result = await self._server.call_tool(name, dict(arguments))
        if getattr(result, "is_error", False):
            raise RuntimeError(f"MCP tool {name} failed")
        return as_object(getattr(result, "structured_content", None))

    async def get_repro(self, installation_id: int, run_id: str) -> dict[str, object]:
        """Read one saved run and its paired outcomes; it must belong to the tenant."""
        return await self.call("get_repro", installation_id=installation_id, run_id=run_id)


class LokiMcpTools(McpToolClient):
    """Loki's read-and-submit tools over MCP, plus its read-only notes."""

    async def recall(self, installation_id: int, repo_id: int) -> dict[str, object]:
        """Read every note maintainers kept for Loki on the repo."""
        return await self.call("recall", installation_id=installation_id, repo_id=repo_id, query="")

    async def get_pr_context(
        self, installation_id: int, pr_id: str, repo_full_name: str
    ) -> dict[str, object]:
        """Read PR metadata and current head."""
        return await self.call(
            "get_pr_context",
            installation_id=installation_id,
            pr_id=pr_id,
            repo_full_name=repo_full_name,
        )

    async def get_diff(
        self, installation_id: int, repo_full_name: str, pr_number: int
    ) -> dict[str, str | bool]:
        """Read the capped diff."""
        values = await self.call(
            "get_diff",
            installation_id=installation_id,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
        )
        return {"text": str(values["text"]), "truncated": values["truncated"] is True}

    async def get_file(
        self,
        installation_id: int,
        pr_id: str,
        repo_full_name: str,
        path: str,
        ref: str,
        start: int = 1,
        end: int | None = None,
    ) -> dict[str, object]:
        """Read a capped line range of a file at the PR head or base."""
        return await self.call(
            "get_file",
            installation_id=installation_id,
            pr_id=pr_id,
            repo_full_name=repo_full_name,
            path=path,
            ref=ref,
            start=start,
            end=end,
        )

    async def save_findings(
        self,
        installation_id: int,
        pr_id: str,
        repo_full_name: str,
        head_sha: str,
        findings: list[dict[str, str | int | None]],
    ) -> dict[str, object]:
        """Submit findings for validation."""
        return await self.call(
            "save_findings",
            installation_id=installation_id,
            pr_id=pr_id,
            repo_full_name=repo_full_name,
            head_sha=head_sha,
            findings=findings,
        )


class HeimdallMcpTools(McpToolClient):
    """Heimdall's card tools over MCP."""

    async def list_findings(self, installation_id: int, pr_id: str) -> list[dict[str, object]]:
        """Load findings for the current head."""
        result = await self.call("list_findings", installation_id=installation_id, pr_id=pr_id)
        rows = result["result"]
        if not isinstance(rows, list):
            raise TypeError("list_findings must return a list")
        return [as_object(row) for row in cast(list[object], rows)]

    async def save_card(
        self,
        installation_id: int,
        pr_id: str,
        number: int,
        title: str,
        head_sha: str,
        rules_summary: str,
        fix_status: str,
        gaps: list[str],
        suggested_action: str,
    ) -> dict[str, str]:
        """Save the review card."""
        result = await self.call(
            "save_card",
            installation_id=installation_id,
            pr_id=pr_id,
            number=number,
            title=title,
            head_sha=head_sha,
            rules_summary=rules_summary,
            fix_status=fix_status,
            gaps=gaps,
            suggested_action=suggested_action,
        )
        return {key: str(value) for key, value in result.items()}

    async def save_draft(
        self, installation_id: int, pr_id: str, head_sha: str, kind: str, body: str
    ) -> dict[str, str]:
        """Save exact draft text and its approval hash."""
        result = await self.call(
            "save_draft",
            installation_id=installation_id,
            pr_id=pr_id,
            head_sha=head_sha,
            kind=kind,
            body=body,
        )
        return {key: str(value) for key, value in result.items()}
