"""Heimdall receives only card, draft, finding and repro context tools."""

import asyncio
import runpy
from pathlib import Path
from typing import cast

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.github.reader import GitHubApiReader

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
create_server = runpy.run_path(str(REPOSITORY_ROOT / "apps/mcp/main.py"))["create_server"]
ALLOWED_TOOLS = runpy.run_path(str(REPOSITORY_ROOT / "agents/heimdall/allowed_tools.py"))[
    "ALLOWED_TOOLS"
]


def test_heimdall_server_exposes_only_its_allowlisted_tools() -> None:
    """Keep Heimdall's tool inventory scoped to its desk responsibilities."""
    sessions = cast(async_sessionmaker[AsyncSession], object())
    reader = cast(GitHubApiReader, object())
    server = create_server(sessions, reader, agent_name="heimdall")

    tools = asyncio.run(server.list_tools())

    assert {tool.name for tool in tools} == ALLOWED_TOOLS
