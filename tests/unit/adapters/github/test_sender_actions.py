"""What the GitHub sender does for each kind of approved draft."""

import uuid
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

import heimdall.adapters.github.sender as sender_module
from heimdall.adapters.db.outbox.models import OutboxMessage
from heimdall.adapters.github.auth import GitHubAppClients
from heimdall.adapters.github.sender import GitHubSender
from heimdall.modules.approval.domain import ApprovedDraft

HEAD = "f" * 40
BODY = "Thanks for this. Closing it for now."


class RecordingClient:
    """Capture every GitHub request, in order."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, object]]] = []

    async def arequest(self, method: str, path: str, **kwargs: object) -> object:
        self.requests.append((method, path, kwargs))
        return object()


class FakeClients:
    def __init__(self, client: RecordingClient) -> None:
        self.client = client

    def for_installation(self, installation_id: int) -> RecordingClient:
        return self.client


def _message() -> OutboxMessage:
    return cast(
        OutboxMessage,
        type(
            "OutboxStub",
            (),
            {
                "id": uuid.uuid4(),
                "installation_id": 12,
                "kind": "github.pr_comment",
                "payload": {
                    "draft_id": str(uuid.uuid4()),
                    "draft_hash": "a" * 64,
                    "repo_full_name": "octo/demo",
                    "pr_number": 7,
                },
                "idempotency_key": "github:draft:test",
            },
        )(),
    )


async def _send(
    monkeypatch: pytest.MonkeyPatch, kind: str, head_sha: str | None = HEAD
) -> RecordingClient:
    async def approved_draft(*args: object) -> ApprovedDraft:
        return ApprovedDraft(
            uuid.uuid4(), uuid.uuid4(), BODY, "a" * 64, "reviewer", "message", kind, head_sha
        )

    monkeypatch.setattr(sender_module, "get_approved_draft", approved_draft)
    client = RecordingClient()
    sender = GitHubSender(
        cast(async_sessionmaker[AsyncSession], object()),
        cast(GitHubAppClients, FakeClients(client)),
    )
    await sender.send(_message())
    return client


@pytest.mark.asyncio
async def test_close_closes_the_pull_request_then_comments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = await _send(monkeypatch, "pr_close")

    assert [(method, path) for method, path, _ in client.requests] == [
        ("PATCH", "/repos/octo/demo/pulls/7"),
        ("POST", "/repos/octo/demo/issues/7/comments"),
    ]
    assert client.requests[0][2]["json"] == {"state": "closed"}
    assert client.requests[1][2]["json"] == {"body": BODY}


@pytest.mark.asyncio
async def test_reopen_reopens_the_pull_request_then_comments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = await _send(monkeypatch, "pr_reopen")

    assert client.requests[0][:2] == ("PATCH", "/repos/octo/demo/pulls/7")
    assert client.requests[0][2]["json"] == {"state": "open"}
    assert client.requests[1][:2] == ("POST", "/repos/octo/demo/issues/7/comments")


@pytest.mark.asyncio
async def test_merge_squashes_at_the_head_the_maintainer_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = await _send(monkeypatch, "pr_merge")

    assert len(client.requests) == 1
    method, path, kwargs = client.requests[0]
    assert (method, path) == ("PUT", "/repos/octo/demo/pulls/7/merge")
    assert kwargs["json"] == {"sha": HEAD, "merge_method": "squash", "commit_message": BODY}


@pytest.mark.asyncio
async def test_merge_is_refused_when_the_approved_head_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="head"):
        await _send(monkeypatch, "pr_merge", head_sha=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["pr_ask", "comment", "intake_reply"])
async def test_every_other_kind_is_only_a_comment(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    client = await _send(monkeypatch, kind)

    assert [(method, path) for method, path, _ in client.requests] == [
        ("POST", "/repos/octo/demo/issues/7/comments")
    ]
