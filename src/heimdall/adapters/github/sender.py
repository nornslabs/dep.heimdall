"""Gjallarhorn: deliver only exact draft text with a matching approval row."""

from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.outbox.models import OutboxMessage
from heimdall.adapters.github._errors import github_errors
from heimdall.adapters.github.auth import GitHubAppClients
from heimdall.modules.approval.api import get_approved_draft
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


def _payload_string(message: OutboxMessage, name: str) -> str:
    """Require a non-empty string field without logging the payload value."""
    value = message.payload.get(name)
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
