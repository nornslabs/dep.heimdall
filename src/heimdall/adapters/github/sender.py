"""Gjallarhorn: deliver only exact draft text with a matching approval row."""

import hashlib
import json
from typing import cast
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.outbox.models import OutboxMessage
from heimdall.adapters.github._errors import github_errors
from heimdall.adapters.github.auth import GitHubAppClients
from heimdall.modules.approval.api import get_approved_draft, verify_repro_workflow_dispatch
from heimdall.shared.context import TenantCtx
from heimdall.shared.github import RepoRef
from heimdall.shared.ids import InstallationId


class GitHubSender:
    """Send approved pull request comments through a GitHub App installation."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        clients: GitHubAppClients,
    ) -> None:
        """Bind tenant database access and authenticated GitHub clients.

        Args:
            sessions: Database sessions used to recheck approval before delivery.
            clients: GitHub App client factory scoped to installations.
        """
        self._sessions = sessions
        self._clients = clients

    async def send(self, message: OutboxMessage) -> None:
        """Deliver one approved comment or reject a stale/unapproved outbox row.

        Args:
            message: GitHub outbox item queued with the approval transaction.

        Raises:
            ValueError: The message kind or required payload fields are invalid.
            DraftChanged: The approval is absent or no longer matches saved text.
            GitHubUnavailable: GitHub is unreachable, rate-limited, or returns 5xx.
            GitHubRequestFailed: GitHub rejects the request permanently.
        """
        if message.kind == "github.workflow_dispatch":
            await self._dispatch_repro_workflow(message)
            return
        if message.kind != "github.pr_comment":
            raise ValueError(f"unsupported GitHub outbox kind: {message.kind}")
        draft_id = _payload_string(message, "draft_id")
        draft_hash = _payload_string(message, "draft_hash")
        repo_name = _payload_string(message, "repo_full_name")
        pr_number = _payload_integer(message, "pr_number")
        installation_id = InstallationId(message.installation_id)
        ctx = TenantCtx(
            installation_id=installation_id,
            request_id=f"outbox-{message.id}",
        )
        approved = await get_approved_draft(self._sessions, ctx, _parse_uuid(draft_id), draft_hash)
        repo = RepoRef(installation_id, repo_name)
        client = self._clients.for_installation(installation_id)
        path = f"/repos/{repo.full_name}/issues/{pr_number}/comments"
        async with github_errors("post pull request comment"):
            await client.arequest(  # pyright: ignore[reportUnknownMemberType]
                "POST",
                path,
                json={"body": approved.body},
                headers={"Idempotency-Key": message.idempotency_key},
            )

    async def _dispatch_repro_workflow(self, message: OutboxMessage) -> None:
        """Dispatch only when the tenant policy record matches every request field."""
        repo_name = _payload_string(message, "repo_full_name")
        workflow = _payload_string(message, "workflow")
        run_id = _parse_uuid(_payload_string(message, "inputs.repro_run_id"))
        approval_id = _parse_uuid(_payload_string(message, "approval_id"))
        request_hash = _payload_string(message, "request_hash")
        raw_inputs = message.payload.get("inputs")
        if not isinstance(raw_inputs, dict):
            raise ValueError("GitHub workflow dispatch inputs are invalid")
        inputs: dict[str, str] = {}
        for key, value in cast(dict[object, object], raw_inputs).items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("GitHub workflow dispatch inputs are invalid")
            inputs[key] = value
        ref = _payload_string(message, "ref")
        request = {
            "repo_full_name": repo_name,
            "workflow": workflow,
            "ref": ref,
            "inputs": inputs,
        }
        computed_hash = hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if computed_hash != request_hash:
            raise ValueError("GitHub workflow dispatch request hash changed")
        installation_id = InstallationId(message.installation_id)
        ctx = TenantCtx(installation_id=installation_id, request_id=f"outbox-{message.id}")
        authorized = await verify_repro_workflow_dispatch(
            self._sessions, ctx, approval_id, run_id, request_hash
        )
        if not authorized:
            raise ValueError("GitHub workflow dispatch has no matching authorization")
        path = f"/repos/{repo_name}/actions/workflows/{workflow}/dispatches"
        async with github_errors("dispatch reproduction workflow"):
            await self._clients.for_installation(installation_id).arequest(  # pyright: ignore[reportUnknownMemberType]
                "POST",
                path,
                json={"ref": ref, "inputs": inputs},
                headers={"Idempotency-Key": message.idempotency_key},
            )


def _payload_string(message: OutboxMessage, name: str) -> str:
    """Require a non-empty string field without logging the payload value."""
    value = message.payload.get(name)
    if name.startswith("inputs."):
        raw_inputs = message.payload.get("inputs")
        if isinstance(raw_inputs, dict):
            inputs = cast(dict[object, object], raw_inputs)
            value = inputs.get(name.partition(".")[2])
        else:
            value = None
    if not isinstance(value, str) or not value:
        raise ValueError(f"GitHub outbox payload is missing {name}")
    return value


def _payload_integer(message: OutboxMessage, name: str) -> int:
    """Require a positive integer field in an outbox payload."""
    value = message.payload.get(name)
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"GitHub outbox payload is missing {name}")
    return value


def _parse_uuid(value: str) -> UUID:
    """Parse a draft UUID from a validated outbox value."""
    try:
        return UUID(value)
    except ValueError as error:
        raise ValueError("GitHub outbox draft id is invalid") from error
