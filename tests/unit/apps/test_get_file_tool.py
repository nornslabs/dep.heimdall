"""Contract and scope tests for the MCP get_file tool (HEI-86)."""

import asyncio
import runpy
import uuid
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from mcp.server.mcpserver import MCPServer
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

import apps.mcp.main as mcp_main
from apps.mcp.main import create_server
from heimdall.adapters.github.reader import GitHubApiReader
from heimdall.modules.triage.api import PrSnapshot
from heimdall.modules.triage.domain import PrState
from heimdall.shared.ids import InstallationId, RepoId

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
HEAD_SHA = "abc1234def5678"
BASE_SHA = "base000deadbeef"
PR_ID = str(uuid.uuid4())
INSTALLATION_ID = 42
REPO = "octo-org/demo"


def _sessions() -> async_sessionmaker[AsyncSession]:
    return cast(async_sessionmaker[AsyncSession], object())


def _reader(file_content: str | None = "alpha\nbeta\ngamma\n") -> GitHubApiReader:
    """Fake reader whose file_at_sha returns a fixed string (or None)."""
    reader = cast(GitHubApiReader, AsyncMock(spec=GitHubApiReader))
    reader.file_at_sha = AsyncMock(return_value=file_content)  # type: ignore[method-assign]
    return reader


def _snapshot(head: str = HEAD_SHA, base: str = BASE_SHA) -> PrSnapshot:
    return PrSnapshot(
        id=UUID(PR_ID),
        repo_id=RepoId(1),
        number=7,
        author="dev",
        head_sha=head,
        base_sha=base,
        linked_issue=None,
        state=PrState.CHECKING,
        state_version=1,
    )


@pytest.fixture
def current_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve one fixed PR snapshot so the tool never needs a database."""

    async def fake_snapshot(*_args: object, **_kwargs: object) -> PrSnapshot:
        return _snapshot()

    monkeypatch.setattr(mcp_main, "_get_pr_snapshot", fake_snapshot)


def _structured(result: object) -> dict[str, object]:
    """Return an MCP tool's structured result as a plain dict."""
    data = getattr(result, "structured_content", None)
    assert isinstance(data, dict)
    return cast(dict[str, object], data)


async def _get_file(server: MCPServer, **overrides: object) -> dict[str, object]:
    """Call get_file with tenant scope and return its structured result."""
    arguments: dict[str, object] = {
        "installation_id": INSTALLATION_ID,
        "pr_id": PR_ID,
        "repo_full_name": REPO,
        "path": "src/main.py",
        "ref": HEAD_SHA,
    }
    arguments.update(overrides)
    return _structured(await server.call_tool("get_file", arguments))


# ---------------------------------------------------------------------------
# Agent scope: which agent tokens expose get_file
# ---------------------------------------------------------------------------


def _tool_names(server: MCPServer) -> set[str]:
    return {tool.name for tool in asyncio.run(server.list_tools())}


@pytest.mark.parametrize("agent_name", ["huginn", "loki"])
def test_review_agents_expose_get_file(agent_name: str) -> None:
    """Huginn and Loki may read files, so their servers publish get_file."""
    assert "get_file" in _tool_names(create_server(_sessions(), _reader(), agent_name=agent_name))


def test_heimdall_server_and_allowlist_exclude_get_file() -> None:
    """Heimdall never reads files; its token neither offers nor allows get_file."""
    allowed = runpy.run_path(str(REPOSITORY_ROOT / "agents/heimdall/allowed_tools.py"))[
        "ALLOWED_TOOLS"
    ]

    assert "get_file" not in _tool_names(create_server(_sessions(), _reader(), "heimdall"))
    assert "get_file" not in allowed


# ---------------------------------------------------------------------------
# Ref scope: only this PR's head or base
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent_name", ["huginn", "loki"])
@pytest.mark.parametrize("ref", [HEAD_SHA, BASE_SHA])
async def test_review_agents_read_a_range_at_head_and_base(
    agent_name: str, ref: str, current_pr: None
) -> None:
    """Huginn and Loki read the requested line range at either end of the PR."""
    reader = _reader("one\ntwo\nthree\nfour\n")
    server = create_server(_sessions(), reader, agent_name=agent_name)

    data = await _get_file(server, ref=ref, start=2, end=3)

    assert data == {
        "found": True,
        "path": "src/main.py",
        "ref": ref,
        "start": 2,
        "end": 3,
        "lines": 2,
        "content": "two\nthree\n",
    }
    cast(AsyncMock, reader.file_at_sha).assert_awaited_once_with(
        mcp_main.RepoRef(InstallationId(INSTALLATION_ID), REPO), "src/main.py", ref
    )


async def test_ref_outside_the_pr_is_refused_without_reading_github(current_pr: None) -> None:
    """A ref that is neither head nor base must be refused before any GitHub call."""
    reader = _reader()
    server = create_server(_sessions(), reader, agent_name="huginn")

    data = await _get_file(server, ref="evilsha999")

    assert data == {"error": "ref is not this PR's head or base"}
    cast(AsyncMock, reader.file_at_sha).assert_not_called()


async def test_end_before_start_is_refused(current_pr: None) -> None:
    """A backwards range is reported as an error instead of returning a wrong slice."""
    reader = _reader()
    server = create_server(_sessions(), reader, agent_name="loki")

    data = await _get_file(server, start=10, end=4)

    assert data == {"error": "end must be at or after start"}
    cast(AsyncMock, reader.file_at_sha).assert_not_called()


async def test_absent_file_is_reported_not_raised(current_pr: None) -> None:
    """A missing file is a valid answer, not a tool failure."""
    server = create_server(_sessions(), _reader(None), agent_name="loki")

    data = await _get_file(server, path="gone.py")

    assert data == {"found": False, "path": "gone.py", "ref": HEAD_SHA, "reason": "not_found"}


# ---------------------------------------------------------------------------
# Line ranges, exercised through the tool
# ---------------------------------------------------------------------------


async def _slice(text: str, **range_arguments: object) -> dict[str, object]:
    """Read a range through get_file with a reader that serves ``text``."""
    server = create_server(_sessions(), _reader(text), agent_name="huginn")
    return await _get_file(server, **range_arguments)


async def test_default_range_reads_the_400_line_cap(current_pr: None) -> None:
    text = "\n".join(str(number) for number in range(1, 500)) + "\n"

    data = await _slice(text)

    assert (data["start"], data["end"], data["lines"]) == (1, 400, 400)


async def test_explicit_range_returns_only_those_lines(current_pr: None) -> None:
    text = "\n".join(f"line{number}" for number in range(1, 11)) + "\n"

    data = await _slice(text, start=3, end=7)

    assert (data["start"], data["end"], data["lines"]) == (3, 7, 5)
    assert data["content"] == "line3\nline4\nline5\nline6\nline7\n"


async def test_end_is_clamped_to_the_cap(current_pr: None) -> None:
    text = "\n".join(str(number) for number in range(1, 600)) + "\n"

    data = await _slice(text, end=500)

    assert (data["end"], data["lines"]) == (400, 400)


async def test_end_is_clamped_to_the_last_line(current_pr: None) -> None:
    data = await _slice("a\nb\nc\n", start=2, end=999)

    assert (data["end"], data["lines"]) == (3, 2)


async def test_start_beyond_the_last_line_returns_no_content(current_pr: None) -> None:
    data = await _slice("only three\nlines here\nfoo\n", start=10)

    assert (data["found"], data["lines"], data["content"]) == (True, 0, "")
    assert data["end"] == 3


async def test_single_line_without_newline_is_one_line(current_pr: None) -> None:
    data = await _slice("no newline at end")

    assert (data["lines"], data["content"]) == (1, "no newline at end")


async def test_start_below_one_is_clamped_to_the_first_line(current_pr: None) -> None:
    data = await _slice("a\nb\n", start=0, end=1)

    assert (data["start"], data["lines"]) == (1, 1)


# ---------------------------------------------------------------------------
# Argument schema
# ---------------------------------------------------------------------------


def test_get_file_schema_requires_tenant_and_ref_arguments() -> None:
    """Tenant scope and ref are required; the line range is optional."""
    tools = asyncio.run(create_server(_sessions(), _reader(), "huginn").list_tools())
    schema = next(tool.input_schema for tool in tools if tool.name == "get_file")

    assert set(schema["required"]) == {
        "installation_id",
        "pr_id",
        "repo_full_name",
        "path",
        "ref",
    }
    assert "start" in schema["properties"]
    assert "end" in schema["properties"]
