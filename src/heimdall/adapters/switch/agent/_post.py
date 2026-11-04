"""Posting as an agent: ``POST /agents/{id}/message``."""

import asyncio

import httpx

from heimdall.adapters.switch.agent.config import POST_TIMEOUT_SECONDS, AgentConnectionConfig
from heimdall.shared.errors import SwitchRequestFailed, SwitchUnavailable


class AgentPoster:
    """Posts room messages as one agent, one request at a time and never retrying.

    A lock keeps posts in call order. There is no retry here on purpose: ``/message`` takes no
    idempotency key, so a blind retry after a timeout could double-post. Callers (the outbox)
    own retries.
    """

    def __init__(
        self, config: AgentConnectionConfig, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        """Create the HTTP client; ``transport`` lets tests substitute one."""
        self._config = config
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "X-Switch-Connection-Id": config.connection_id,
            },
            timeout=POST_TIMEOUT_SECONDS,
            transport=transport,
        )

    async def post(self, room_id: str, content: str) -> None:
        """Post ``content`` to ``room_id`` as this agent.

        Args:
            room_id: Room to post in.
            content: Message text.

        Raises:
            SwitchUnavailable: Timeout, network failure, 429 or a 5xx; safe to retry later.
            SwitchRequestFailed: Switch rejected the request; retrying will not help.
        """
        async with self._lock:
            response = await self._send(room_id, content)
        if response.is_success:
            return
        if response.status_code >= 500 or response.status_code == 429:
            raise SwitchUnavailable(f"Switch returned {response.status_code}")
        raise SwitchRequestFailed(f"Switch returned {response.status_code}")

    async def aclose(self) -> None:
        """Close the pooled HTTP client."""
        await self._client.aclose()

    async def _send(self, room_id: str, content: str) -> httpx.Response:
        """Send the request, mapping transport failures to ``SwitchUnavailable``."""
        try:
            return await self._client.post(
                self._config.message_url, json={"room_id": room_id, "content": content}
            )
        except httpx.TimeoutException as error:
            raise SwitchUnavailable("Switch request timed out") from error
        except httpx.RequestError as error:
            raise SwitchUnavailable("Switch could not be reached") from error
