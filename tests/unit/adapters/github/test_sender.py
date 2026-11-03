"""GitHub outbox sender approval gate and exact comment payload."""

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
from heimdall.shared.errors import DraftChanged


class FakeClient:
    """Capture GitHub requests without network access."""

    def __init__(self) -> None:
        self.request: tuple[str, str, dict[str, object]] | None = None

    async def arequest(self, method: str, path: str, **kwargs: object) -> object:
        self.request = (method, path, kwargs)
        return object()


class FakeClients:
    """Return a single installation-scoped test client."""

    def __init__(self, client: FakeClient) -> None:
        self.client = client

    def for_installation(self, installation_id: int) -> FakeClient:
        return self.client


def _message(draft_hash: str) -> OutboxMessage:
    """Create a valid outbox payload for the sender."""
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
                    "draft_hash": draft_hash,
                    "repo_full_name": "octo/demo",
                    "pr_number": 7,
                },
                "idempotency_key": "github:draft:test",
            },
        )(),
    )


@pytest.mark.asyncio
async def test_sender_posts_exact_text_only_after_matching_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recheck approval hash and post the exact approved body."""
    body = "Exact maintainer approved text"
    draft_hash = "a" * 64
    draft_id = uuid.uuid4()

    async def approved_draft(*args: object) -> ApprovedDraft:
        return ApprovedDraft(draft_id, uuid.uuid4(), body, draft_hash, "reviewer", "reaction")

    monkeypatch.setattr(sender_module, "get_approved_draft", approved_draft)
    client = FakeClient()
    sender = GitHubSender(
        cast(async_sessionmaker[AsyncSession], object()),
        cast(GitHubAppClients, FakeClients(client)),
    )
    message = _message(draft_hash)
    message.payload["draft_id"] = str(draft_id)

    await sender.send(message)

    assert client.request is not None
    method, path, kwargs = client.request
    assert method == "POST"
    assert path == "/repos/octo/demo/issues/7/comments"
    assert kwargs["json"] == {"body": body}
    assert kwargs["headers"] == {"Idempotency-Key": message.idempotency_key}


@pytest.mark.asyncio
async def test_sender_never_posts_when_approval_check_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop before the GitHub request when no matching approval is found."""

    async def missing_approval(*args: object) -> ApprovedDraft:
        raise DraftChanged("draft has no matching approval")

    monkeypatch.setattr(sender_module, "get_approved_draft", missing_approval)
    client = FakeClient()
    sender = GitHubSender(
        cast(async_sessionmaker[AsyncSession], object()),
        cast(GitHubAppClients, FakeClients(client)),
    )

    with pytest.raises(DraftChanged):
        await sender.send(_message("wrong-hash"))

    assert client.request is None
