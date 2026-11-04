"""Agent tool clients that call the real MCP server in-process, plus a scripted model for Loki.

Going through ``create_server`` keeps the per-agent tool scoping and the server's argument
validation in the path, the same way a stdio MCP session would.
"""

from collections.abc import Sequence
from typing import cast

from mcp.server.mcpserver import MCPServer

from heimdall.adapters.ai import ChatMessage, ChatResponse, ToolChoice, ToolSpec, Usage
from tests.e2e.fake_world import as_object

SCRIPTED_MODEL = "scripted-e2e"


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


class LokiMcpTools(McpToolClient):
    """Loki's three read-and-submit tools over MCP."""

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

    async def get_repro(self, installation_id: int, run_id: str) -> dict[str, object]:
        """Load one paired reproduction result."""
        return await self.call("get_repro", installation_id=installation_id, run_id=run_id)

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


class ScriptedModel:
    """A ``ChatModel`` that always answers with one fixed reply, for Loki's single call."""

    def __init__(self, reply: str) -> None:
        """Keep the reply text the model returns."""
        self._reply = reply
        self.calls: list[tuple[ChatMessage, ...]] = []

    @property
    def model(self) -> str:
        """The model name recorded in usage."""
        return SCRIPTED_MODEL

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        *,
        tool_choice: ToolChoice = "auto",
    ) -> ChatResponse:
        """Record the prompt and return the scripted reply."""
        self.calls.append(tuple(messages))
        return ChatResponse(self._reply, (), Usage(0, 0), SCRIPTED_MODEL)
