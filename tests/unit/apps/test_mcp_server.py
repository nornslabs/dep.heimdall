"""MCP server exposes the supported reproduction tools."""

import asyncio
import runpy
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.github.reader import GitHubApiReader

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
create_server = runpy.run_path(str(REPOSITORY_ROOT / "apps/mcp/main.py"))["create_server"]


def test_mcp_server_registers_reproduction_tools() -> None:
    """Publish start, retrieval and file-reading tools with typed schemas."""
    sessions = cast(async_sessionmaker[AsyncSession], object())
    reader = cast(GitHubApiReader, object())
    server = create_server(sessions, reader)

    tools = asyncio.run(server.list_tools())

    assert {tool.name for tool in tools} == {
        "get_pr_context",
        "get_diff",
        "get_file",
        "start_repro",
        "get_repro",
        "recall",
    }
    assert all("installation_id" in tool.input_schema["properties"] for tool in tools)


@pytest.mark.parametrize(
    ("agent_name", "expected_tools"),
    [
        (
            "huginn",
            {
                "get_pr_context",
                "get_diff",
                "get_file",
                "start_repro",
                "get_repro",
                "recall",
            },
        ),
        (
            "loki",
            {"get_pr_context", "get_diff", "save_findings", "recall", "get_file"},
        ),
        (
            "heimdall",
            {
                "get_pr_context",
                "list_findings",
                "get_repro",
                "save_card",
                "save_draft",
                "draft_close",
                "draft_reopen",
                "draft_merge",
                "draft_ask_contributor",
                "recheck_intake",
                "create_mechanical_task",
                "recall",
            },
        ),
    ],
)
def test_each_agent_mcp_contract_is_tenant_scoped(
    agent_name: str,
    expected_tools: set[str],
) -> None:
    """Publish only the selected agent's tools with explicit tenant inputs."""
    sessions = cast(async_sessionmaker[AsyncSession], object())
    reader = cast(GitHubApiReader, object())
    server = create_server(sessions, reader, agent_name=agent_name)

    tools = asyncio.run(server.list_tools())

    assert {tool.name for tool in tools} == expected_tools
    for tool in tools:
        assert "installation_id" in tool.input_schema["properties"]


def test_loki_server_exposes_only_its_allowlisted_tools() -> None:
    """Do not register repro controls on Loki's MCP server instance."""
    sessions = cast(async_sessionmaker[AsyncSession], object())
    reader = cast(GitHubApiReader, object())
    server = create_server(sessions, reader, agent_name="loki")

    tools = asyncio.run(server.list_tools())

    assert {tool.name for tool in tools} == {
        "get_pr_context",
        "get_diff",
        "save_findings",
        "recall",
        "get_file",
    }


@pytest.mark.parametrize("agent_name", ["huginn", "loki", "heimdall"])
def test_no_agent_is_offered_a_memory_write_tool(agent_name: str) -> None:
    """Notes are written only by a maintainer's chat command, never by a model's tool call."""
    sessions = cast(async_sessionmaker[AsyncSession], object())
    reader = cast(GitHubApiReader, object())
    server = create_server(sessions, reader, agent_name=agent_name)

    names = {tool.name for tool in asyncio.run(server.list_tools())}

    assert "recall" in names
    assert not names & {"remember", "forget"}
