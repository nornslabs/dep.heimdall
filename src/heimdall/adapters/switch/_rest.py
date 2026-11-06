"""Mattermost REST operations used by the Switch room adapter."""

from typing import Final, cast

import httpx

from heimdall.adapters.switch.ports import RoomPost
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
            "/api/v4/reactions",
            json={"user_id": "me", "post_id": post_id, "emoji_name": emoji_name},
        )

    async def remove_reaction(self, post_id: str, emoji_name: str) -> None:
        """Remove the authenticated bot user's reaction from a post."""
        await self._request(
            "DELETE",
            f"/api/v4/users/me/posts/{post_id}/reactions/{emoji_name}",
        )

    async def get_post(self, post_id: str) -> RoomPost:
        """Fetch one Mattermost post by id."""
        response = await self._request("GET", f"{POSTS_PATH}/{post_id}")
        try:
            payload = response.json()
            return RoomPost(
                id=_required_text(payload, "id"),
                channel_id=_required_text(payload, "channel_id"),
                root_id=_optional_text(payload, "root_id"),
                message=_required_text(payload, "message"),
            )
        except (ValueError, TypeError, KeyError) as error:
            raise SwitchRequestFailed("Switch returned invalid post data") from error

    async def get_thread_posts(self, root_id: str) -> tuple[RoomPost, ...]:
        """Fetch a thread's posts from Mattermost, oldest first."""
        response = await self._request("GET", f"{POSTS_PATH}/{root_id}/thread")
        try:
            posts = cast(dict[str, object], response.json())["posts"]
            entries = sorted(
                cast(dict[str, dict[str, object]], posts).values(),
                key=lambda post: int(cast(int, post["create_at"])),
            )
            return tuple(
                RoomPost(
                    id=_required_text(entry, "id"),
                    channel_id=_required_text(entry, "channel_id"),
                    root_id=_optional_text(entry, "root_id"),
                    message=_optional_text(entry, "message"),
                )
                for entry in entries
            )
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise SwitchRequestFailed("Switch returned invalid thread data") from error

    async def get_username(self, user_id: str) -> str:
        """Resolve a Mattermost user id to its username."""
        response = await self._request("GET", f"/api/v4/users/{user_id}")
        try:
            return _required_text(response.json(), "username")
        except (ValueError, TypeError, KeyError) as error:
            raise SwitchRequestFailed("Switch returned invalid user data") from error

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


def _required_text(payload: object, key: str) -> str:
    """Read a non-empty string field from a Mattermost response."""
    if not isinstance(payload, dict):
        raise TypeError("Switch response must be an object")
    fields = cast(dict[str, object], payload)
    value = fields.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Switch response has invalid {key}")
    return value


def _optional_text(payload: object, key: str) -> str:
    """Read a string field that Mattermost may leave empty."""
    if not isinstance(payload, dict):
        raise TypeError("Switch response must be an object")
    fields = cast(dict[str, object], payload)
    value = fields.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"Switch response has invalid {key}")
    return value
