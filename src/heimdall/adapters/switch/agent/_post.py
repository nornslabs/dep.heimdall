"""Posting as an agent: ``POST /agents/{id}/message``."""

import asyncio
from typing import cast

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

    async def post_in_thread(self, content: str, thread_id: str | None) -> str:
        """Post ``content`` as this agent, as a thread root or as a reply, and return its id.

        This uses Switch's ``post_message`` operation, the only agent call that threads. The
        operation has no room argument: it posts to the room the agent's live connection has
        claimed, which is why the request names that connection. An agent that claims several
        rooms is refused with a 400 and the row dies, instead of posting to a guessed room.

        Args:
            content: Message text.
            thread_id: Event id of a message in the thread to reply into, or None to post a
                new top-level message whose id can root a thread.

        Returns:
            The Switch event id of the posted message.

        Raises:
            SwitchUnavailable: Timeout, network failure, 429 or a 5xx; safe to retry later.
            SwitchRequestFailed: Switch refused the request, or its reply had no event id.
        """
        body: dict[str, str] = {"body": content}
        if thread_id is not None:
            body["thread_id"] = thread_id
        async with self._lock:
            response = await self._send_operation(body)
        if response.status_code >= 500 or response.status_code == 429:
            raise SwitchUnavailable(f"Switch returned {response.status_code}")
        if not response.is_success:
            raise SwitchRequestFailed(f"Switch returned {response.status_code}")
        return _event_id(response)

    async def aclose(self) -> None:
        """Close the pooled HTTP client."""
        await self._client.aclose()

    async def _send_operation(self, body: dict[str, str]) -> httpx.Response:
        """Call the ``post_message`` operation, mapping transport failures to unavailable."""
        try:
            return await self._client.post(self._config.post_message_url, json=body)
        except httpx.TimeoutException as error:
            raise SwitchUnavailable("Switch request timed out") from error
        except httpx.RequestError as error:
            raise SwitchUnavailable("Switch could not be reached") from error

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


def _event_id(response: httpx.Response) -> str:
    """Read the posted message's event id from an operation reply, or fail without echoing it."""
    try:
        payload: object = response.json()
    except ValueError:
        raise SwitchRequestFailed("Switch reply is not JSON") from None
    event_id = _field(_field(payload, "result"), "event_id")
    if not isinstance(event_id, str) or not event_id:
        raise SwitchRequestFailed("Switch reply has no event id")
    return event_id


def _field(value: object, name: str) -> object:
    """Read ``name`` from a JSON object, or return None when ``value`` is not one."""
    if not isinstance(value, dict):
        return None
    fields = cast(dict[str, object], value)
    return fields.get(name)
