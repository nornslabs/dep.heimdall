"""Mattermost REST operations used by the Switch room adapter."""

from typing import Final

import httpx

from heimdall.shared.errors import SwitchRequestFailed, SwitchUnavailable

POSTS_PATH: Final = "/api/v4/posts"


class MattermostRest:
    """Authenticated REST client for Switch's Mattermost room transport."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Create the API client with an optional transport for isolated tests."""
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            transport=transport,
        )

    async def create_post(
        self,
        channel_id: str,
        message: str,
        *,
        root_id: str = "",
        idempotency_key: str = "",
    ) -> str:
        """Create a room post or thread reply and return its server id."""
        props = {"heimdall_outbox_id": idempotency_key} if idempotency_key else {}
        response = await self._request(
            "POST",
            POSTS_PATH,
            json={
                "channel_id": channel_id,
                "message": message,
                "root_id": root_id,
                "props": props,
            },
        )
        try:
            post_id = response.json()["id"]
        except (ValueError, KeyError, TypeError) as error:
            raise SwitchRequestFailed("Switch returned an invalid post response") from error
        if not isinstance(post_id, str) or not post_id:
            raise SwitchRequestFailed("Switch returned an invalid post id")
        return post_id

    async def add_reaction(self, post_id: str, emoji_name: str) -> None:
        """Add a reaction as the authenticated bot user."""
        await self._request(
            "POST",
            f"/api/v4/users/me/posts/{post_id}/reactions",
            json={"user_id": "me", "post_id": post_id, "emoji_name": emoji_name},
        )

    async def aclose(self) -> None:
        """Close the pooled HTTP client."""
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, object] | None = None,
    ) -> httpx.Response:
        """Send a request and map status and transport errors to domain errors."""
        try:
            response = await self._client.request(method, f"{self._base_url}{path}", json=json)
        except httpx.TimeoutException as error:
            raise SwitchUnavailable("Switch request timed out") from error
        except httpx.RequestError as error:
            raise SwitchUnavailable("Switch could not be reached") from error
        if response.is_success:
            return response
        if response.status_code >= 500 or response.status_code == 429:
            raise SwitchUnavailable(f"Switch returned {response.status_code}")
        raise SwitchRequestFailed(f"Switch returned {response.status_code}")
