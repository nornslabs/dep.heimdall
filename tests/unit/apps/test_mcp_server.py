"""MCP server exposes the supported reproduction tools."""

import asyncio
import runpy
from pathlib import Path
from typing import cast

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.github.reader import GitHubApiReader

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
create_server = runpy.run_path(str(REPOSITORY_ROOT / "apps/mcp/main.py"))["create_server"]


def test_mcp_server_registers_reproduction_tools() -> None:
    """Publish start and retrieval tools with typed schemas."""
    sessions = cast(async_sessionmaker[AsyncSession], object())
    server = create_server(sessions)

    tools = asyncio.run(server.list_tools())

    assert {tool.name for tool in tools} == {"start_repro", "get_repro"}
    assert "installation_id" in tools[0].input_schema["properties"]


def test_loki_server_exposes_only_its_allowlisted_tools() -> None:
    """Do not register repro controls on Loki's MCP server instance."""
    sessions = cast(async_sessionmaker[AsyncSession], object())
    reader = cast(GitHubApiReader, object())
    server = create_server(sessions, reader, agent_name="loki")

    tools = asyncio.run(server.list_tools())

    assert {tool.name for tool in tools} == {"get_pr_context", "get_diff", "save_findings"}
