"""Loki's MCP memory path: ``get_pr_context`` names the repo, ``recall`` reads its notes."""

import json
import uuid
from typing import cast

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from agents._mcp_tools import LokiMcpTools, as_object
from apps.mcp.main import create_server
from heimdall.adapters.github.reader import GitHubApiReader
from heimdall.modules.memory.api import NoteScope, remember_note
from heimdall.modules.tenancy.api import on_installation_event
from heimdall.modules.triage.api import EventKind, Facts, PrEvent, handle_event
from heimdall.shared.agents import AgentName
from heimdall.shared.context import TenantCtx
from heimdall.shared.github import PullRequestInfo, RepoRef
from heimdall.shared.ids import InstallationId, RepoId
from tests.integration.db.installation_helpers import created_installation

pytestmark = pytest.mark.integration


def _fresh_id() -> int:
    return uuid.uuid4().int % 1_000_000_000 + 1


async def _repo_with_notes(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> tuple[int, int]:
    """Install a repo maintained by alice with one Loki note and one Huginn note."""
    installation_id, repo_id = _fresh_id(), _fresh_id()
    await on_installation_event(
        sessions, created_installation(installation_id, "octo-org", repo_id, f"octo-org/r{repo_id}")
    )
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE repos SET config = CAST(:config AS jsonb) WHERE repo_id = :id"),
            {"config": json.dumps({"maintainers": ["alice"]}), "id": repo_id},
        )
    ctx = TenantCtx(InstallationId(installation_id), "recall-test")
    for agent, key in ((AgentName.LOKI, "scope"), (AgentName.HUGINN, "test-command")):
        await remember_note(
            sessions, ctx, NoteScope(RepoId(repo_id), agent), key, f"{key} note", "alice", "m-1"
        )
    return installation_id, repo_id


async def _recall(
    sessions: async_sessionmaker[AsyncSession], agent: str, installation_id: int, repo_id: int
) -> list[dict[str, object]]:
    unused_reader = cast(GitHubApiReader, object())
    server = create_server(sessions, unused_reader, agent_name=agent)
    result = await server.call_tool(
        "recall", {"installation_id": installation_id, "repo_id": repo_id, "query": ""}
    )
    content = as_object(getattr(result, "structured_content", None))
    return cast(list[dict[str, object]], content["notes"])


async def test_recall_returns_the_bound_agents_notes(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    installation_id, repo_id = await _repo_with_notes(sessions, engine)

    notes = await _recall(sessions, "loki", installation_id, repo_id)

    assert notes == [{"key": "scope", "value": "scope note", "created_by": "alice"}]


async def test_recall_never_returns_another_installations_notes(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    _, repo_id = await _repo_with_notes(sessions, engine)
    other_installation, _ = await _repo_with_notes(sessions, engine)

    assert await _recall(sessions, "loki", other_installation, repo_id) == []


class PullRequestReader:
    """Answer ``get_pull_request`` with fixed details, as GitHub would."""

    async def get_pull_request(self, repo: RepoRef, number: int) -> PullRequestInfo:
        """Return the PR details for any number."""
        return PullRequestInfo(
            number=number,
            title="Fix pager",
            body="",
            author="octocat",
            head_sha="head-1",
            base_sha="base-1",
            labels=(),
            assignees=(),
            state="open",
            draft=False,
        )


async def test_get_pr_context_names_the_prs_repo_for_recall(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    installation_id, repo_id = await _repo_with_notes(sessions, engine)
    ctx = TenantCtx(InstallationId(installation_id), "recall-test")
    full_name = f"octo-org/r{repo_id}"
    await handle_event(
        sessions,
        ctx,
        PrEvent(
            event_id=f"open-{repo_id}",
            repo_id=RepoId(repo_id),
            repo_full_name=full_name,
            number=3,
            author="octocat",
            head_sha="head-1",
            base_sha="base-1",
            kind=EventKind.PR_OPENED,
            facts=Facts(),
        ),
    )
    async with engine.connect() as conn:
        pr_id = (
            await conn.execute(text("SELECT id FROM prs WHERE repo_id = :id"), {"id": repo_id})
        ).scalar_one()
    reader = cast(GitHubApiReader, PullRequestReader())
    server = create_server(sessions, reader, agent_name="loki")

    result = await server.call_tool(
        "get_pr_context",
        {"installation_id": installation_id, "pr_id": str(pr_id), "repo_full_name": full_name},
    )

    assert as_object(getattr(result, "structured_content", None))["repo_id"] == repo_id


async def test_lokis_mcp_client_recalls_its_notes(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    installation_id, repo_id = await _repo_with_notes(sessions, engine)
    reader = cast(GitHubApiReader, PullRequestReader())
    tools = LokiMcpTools(create_server(sessions, reader, agent_name="loki"))

    result = await tools.recall(installation_id, repo_id)

    assert result["notes"] == [{"key": "scope", "value": "scope note", "created_by": "alice"}]
