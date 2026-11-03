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


@pytest.mark.asyncio
async def test_sender_dispatches_only_an_authorized_exact_workflow_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recheck policy authorization and preserve the approved workflow inputs."""
    import hashlib
    import json

    run_id = uuid.uuid4()
    approval_id = uuid.uuid4()
    request = {
        "repo_full_name": "octo/demo",
        "workflow": "heimdall-repro.yml",
        "ref": "base-sha",
        "inputs": {
            "repro_run_id": str(run_id),
            "pr_number": "7",
            "head_sha": "head-sha",
            "base_sha": "base-sha",
            "test_cmd": "pytest -q",
        },
    }
    request_hash = hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    message = cast(
        OutboxMessage,
        type(
            "OutboxStub",
            (),
            {
                "id": uuid.uuid4(),
                "installation_id": 12,
                "kind": "github.workflow_dispatch",
                "payload": {
                    **request,
                    "approval_id": str(approval_id),
                    "request_hash": request_hash,
                },
                "idempotency_key": "github:repro-dispatch:test",
            },
        )(),
    )

    async def authorized(*args: object) -> bool:
        return True

    monkeypatch.setattr(sender_module, "verify_repro_workflow_dispatch", authorized)
    client = FakeClient()
    sender = GitHubSender(
        cast(async_sessionmaker[AsyncSession], object()),
        cast(GitHubAppClients, FakeClients(client)),
    )
    await sender.send(message)
    assert client.request is not None
    method, path, kwargs = client.request
    assert method == "POST"
    assert path == "/repos/octo/demo/actions/workflows/heimdall-repro.yml/dispatches"
    assert kwargs["json"] == {"ref": "base-sha", "inputs": request["inputs"]}


@pytest.mark.asyncio
async def test_sender_rejects_modified_workflow_dispatch_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject altered dispatch inputs before checking authorization or calling GitHub."""
    message = _message("unused")
    message.kind = "github.workflow_dispatch"
    message.payload.update(
        {
            "repo_full_name": "octo/demo",
            "workflow": "heimdall-repro.yml",
            "ref": "base-sha",
            "inputs": {"repro_run_id": str(uuid.uuid4())},
            "approval_id": str(uuid.uuid4()),
            "request_hash": "incorrect",
        }
    )

    async def authorization_must_not_be_checked(*args: object) -> bool:
        pytest.fail("modified payload reached approval lookup")

    monkeypatch.setattr(
        sender_module, "verify_repro_workflow_dispatch", authorization_must_not_be_checked
    )
    client = FakeClient()
    sender = GitHubSender(
        cast(async_sessionmaker[AsyncSession], object()),
        cast(GitHubAppClients, FakeClients(client)),
    )
    with pytest.raises(ValueError, match="request hash changed"):
        await sender.send(message)
    assert client.request is None
