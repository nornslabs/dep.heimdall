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

    Also provides feedback features (typing, reactions, progress posts) via Mattermost REST API
    when bot token and team ID are configured.
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
        # Separate client for Mattermost REST API (bot token) for feedback features
        self._mm_client: httpx.AsyncClient | None = None
        if config.mattermost_bot_token and config.mattermost_team_id and config.mattermost_base_url:
            self._mm_client = httpx.AsyncClient(
                headers={"Authorization": f"Bearer {config.mattermost_bot_token}"},
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

    async def send_typing(self, channel_id: str) -> None:
        """Send a typing indicator for this agent in the given channel.

        Uses Mattermost REST typing endpoint.
        No-op if bot token or team ID is not configured. Fire-and-forget: errors are logged
        but not raised to avoid disrupting the main flow.

        Args:
            channel_id: Mattermost channel (room) ID.
        """
        if not self._mm_client:
            return
        base = self._config.mattermost_base_url  # pyright: ignore[reportOptionalMemberAccess]
        assert base is not None
        team = self._config.mattermost_team_id  # pyright: ignore[reportOptionalMemberAccess]
        try:
            url = f"{base.rstrip('/')}/api/v4/users/me/teams/{team}/channels/{channel_id}/typing"
            await self._mm_client.post(url)
        except Exception:
            # Silently ignore - typing is a best-effort UX feature
            pass

    async def add_reaction(self, post_id: str, emoji_name: str) -> None:
        """Add a reaction to a post as the bot user.

        Uses Mattermost REST API. No-op if bot token not configured.

        Args:
            post_id: The post to react to.
            emoji_name: Emoji name (e.g., "eyes", "white_check_mark", "x").
        """
        if not self._mm_client:
            return
        base = self._config.mattermost_base_url  # pyright: ignore[reportOptionalMemberAccess]
        assert base is not None
        try:
            url = f"{base.rstrip('/')}/api/v4/users/me/posts/{post_id}/reactions"
            await self._mm_client.post(
                url, json={"user_id": "me", "post_id": post_id, "emoji_name": emoji_name}
            )
        except Exception:
            pass

    async def remove_reaction(self, post_id: str, emoji_name: str) -> None:
        """Remove the bot user's reaction from a post.

        No-op if bot token not configured.

        Args:
            post_id: The post to remove reaction from.
            emoji_name: Emoji name.
        """
        if not self._mm_client:
            return
        base = self._config.mattermost_base_url  # pyright: ignore[reportOptionalMemberAccess]
        assert base is not None
        try:
            url = f"{base.rstrip('/')}/api/v4/users/me/posts/{post_id}/reactions/{emoji_name}"
            await self._mm_client.delete(url)
        except Exception:
            pass

    async def post_progress(
        self,
        channel_id: str,
        thread_id: str | None,
        text: str,
        *,
        update_post_id: str | None = None,
    ) -> str | None:
        """Post or update one progress message and return its post id.

        Creates with ``POST /api/v4/posts``, rooted in ``thread_id`` when given, and updates
        with ``PATCH /api/v4/posts/{post_id}``. A progress post is a courtesy feature: it is
        a no-op when the bot is not configured, and failures are swallowed so a slow Mattermost
        never delays the reply.

        Args:
            channel_id: Channel (room) to post in.
            thread_id: Thread to root the post in, or None for a top-level post.
            text: Progress message text.
            update_post_id: Id of a post created by an earlier call, to update in place.

        Returns:
            The post id, usable as ``update_post_id`` on the next call, or None when the bot
            is not configured or the request failed.
        """
        if not self._mm_client:
            return None
        base = self._config.mattermost_base_url  # pyright: ignore[reportOptionalMemberAccess]
        assert base is not None
        base = base.rstrip("/")  # pyright: ignore[reportOptionalMemberAccess]
        try:
            if update_post_id is not None:
                await self._mm_client.patch(
                    f"{base}/api/v4/posts/{update_post_id}", json={"message": text}
                )
                return update_post_id
            response = await self._mm_client.post(
                f"{base}/api/v4/posts",
                json={"channel_id": channel_id, "root_id": thread_id or "", "message": text},
            )
        except Exception:
            # Silence network failures: the reply itself is what matters.
            return None
        return _post_id(response) if response.is_success else None

    async def post_rich(
        self,
        room_id: str,
        content: str,
        thread_id: str | None,
        *,
        attachments: list[dict[str, object]],
    ) -> str | None:
        """Post one rich message with Mattermost attachments.

        Unlike ``post_progress`` this is the reply itself, so transport and HTTP failures are
        raised for the outbox to retry rather than swallowed. Returns None (without posting)
        when the bot is not configured: callers fall back to plain markdown.

        Args:
            room_id: Channel (room) to post in.
            content: Fallback message text shown when attachments are not rendered.
            thread_id: Thread to root the post in, or None for a top-level post.
            attachments: Mattermost attachment definitions for the rich card.
            update_post_id: Id of a post created by an earlier call, to update in place.

        Returns:
            The post id, or None when the bot is not configured.

        Raises:
            SwitchUnavailable: the Mattermost API could not be reached or refused temporarily.
            SwitchRequestFailed: the Mattermost API rejected the request.
        """
        if not self._mm_client:
            return None
        base = self._config.mattermost_base_url  # pyright: ignore[reportOptionalMemberAccess]
        assert base is not None
        base = base.rstrip("/")  # pyright: ignore[reportOptionalMemberAccess]
        payload: dict[str, object] = {
            "channel_id": room_id,
            "root_id": thread_id or "",
            "message": content,
            "props": {"attachments": attachments},
        }
        async with self._lock:
            try:
                response = await self._mm_client.post(f"{base}/api/v4/posts", json=payload)
            except httpx.TimeoutException as error:
                raise SwitchUnavailable("Mattermost request timed out") from error
            except httpx.RequestError as error:
                raise SwitchUnavailable("Mattermost could not be reached") from error
        if response.status_code >= 500 or response.status_code == 429:
            raise SwitchUnavailable(f"Mattermost returned {response.status_code}")
        if not response.is_success:
            raise SwitchRequestFailed(f"Mattermost returned {response.status_code}")
        post_id = _post_id(response)
        if post_id is None:
            raise SwitchRequestFailed("Mattermost reply has no post id")
        return post_id

    async def aclose(self) -> None:
        """Close the pooled HTTP clients."""
        await self._client.aclose()
        if self._mm_client:
            await self._mm_client.aclose()

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


def _post_id(response: httpx.Response) -> str | None:
    """Read the created post's id from a Mattermost posts reply, or None when it has none."""
    try:
        payload: object = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    fields = cast(dict[str, object], payload)
    post_id = fields.get("id")
    return post_id if isinstance(post_id, str) and post_id else None


def _field(value: object, name: str) -> object:
    """Read ``name`` from a JSON object, or return None when ``value`` is not one."""
    if not isinstance(value, dict):
        return None
    fields = cast(dict[str, object], value)
    return fields.get(name)
