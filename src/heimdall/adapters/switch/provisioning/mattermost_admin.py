"""Prepare Mattermost for Heimdall: the triage channel, its members and the bot's API token."""

from collections.abc import Sequence
from types import TracebackType
from typing import Final, Self

import httpx

from heimdall.adapters.switch.provisioning._http import (
    PROVISIONING_TIMEOUT_SECONDS,
    SwitchRejected,
    send_checked,
)
from heimdall.shared.errors import SwitchUnavailable

API_PREFIX: Final = "/api/v4"
_SERVICE: Final = "Mattermost"
_NOT_FOUND: Final = 404
_OPEN_CHANNEL: Final = "O"


class MattermostAdmin:
    """Signs in once as the Mattermost admin, then sets up the channel and token Heimdall needs."""

    def __init__(
        self,
        base_url: str,
        login: str,
        password: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Create the HTTP client; the session token is added after the first sign-in.

        Args:
            base_url: Mattermost root, for example ``http://localhost:8065``.
            login: Admin username or email.
            password: Admin password. Never logged.
            transport: Optional replacement transport for tests.
        """
        self._login = login
        self._password = password
        self._signed_in = False
        self._client = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}{API_PREFIX}",
            timeout=PROVISIONING_TIMEOUT_SECONDS,
            transport=transport,
        )

    @staticmethod
    async def token_works(base_url: str, token: str) -> bool:
        """Tell whether Mattermost still accepts an access token.

        Args:
            base_url: Mattermost root.
            token: The token to test. Never logged.

        Returns:
            True when the token authenticates, False when Mattermost refuses it.

        Raises:
            SwitchUnavailable: Mattermost was unreachable or failed; the answer is unknown.
        """
        async with httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}{API_PREFIX}",
            timeout=PROVISIONING_TIMEOUT_SECONDS,
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            try:
                response = await client.get("/users/me")
            except httpx.HTTPError as error:
                raise SwitchUnavailable(
                    f"Mattermost unreachable while checking the token: {type(error).__name__}"
                ) from error
        return response.is_success

    async def team_id(self, team_name: str) -> str:
        """Look up a team's id by its URL slug.

        Args:
            team_name: Team URL slug, for example ``heimdall``.

        Returns:
            The team id.

        Raises:
            SwitchUnavailable: Mattermost was unreachable or failed; safe to retry.
            SwitchRequestFailed: Sign-in or the lookup was refused (unknown team, bad password).
        """
        response = await self._send("GET", f"/teams/name/{team_name}", f"finding team {team_name}")
        return str(response.json()["id"])

    async def ensure_channel(self, team_id: str, name: str) -> str:
        """Return the id of an open channel, creating it when it does not exist.

        Args:
            team_id: Team that owns the channel.
            name: Channel URL name, for example ``triage``.

        Returns:
            The channel id (what ``HEIMDALL_DEFAULT_SWITCH_ROOM_ID`` holds).

        Raises:
            SwitchUnavailable: Mattermost was unreachable or failed; safe to retry.
            SwitchRequestFailed: A request was refused.
        """
        try:
            found = await self._send(
                "GET", f"/teams/{team_id}/channels/name/{name}", f"finding channel {name}"
            )
            return str(found.json()["id"])
        except SwitchRejected as error:
            if error.status != _NOT_FOUND:
                raise
        created = await self._send(
            "POST",
            "/channels",
            f"creating channel {name}",
            {
                "team_id": team_id,
                "name": name,
                "display_name": name.title(),
                "type": _OPEN_CHANNEL,
            },
        )
        return str(created.json()["id"])

    async def add_members(self, channel_id: str, usernames: Sequence[str]) -> None:
        """Add users to a channel by username; adding an existing member is harmless.

        Args:
            channel_id: Channel to add them to.
            usernames: Mattermost usernames, for example the three agent bots.

        Raises:
            SwitchUnavailable: Mattermost was unreachable or failed; safe to retry.
            SwitchRequestFailed: A user does not exist or a request was refused. The message
                names the user.
        """
        for username in usernames:
            user = await self._send("GET", f"/users/username/{username}", f"finding {username}")
            await self._send(
                "POST",
                f"/channels/{channel_id}/members",
                f"adding {username} to the channel",
                {"user_id": str(user.json()["id"])},
            )

    async def create_access_token(self, description: str) -> str:
        """Enable personal access tokens and create one for the signed-in admin.

        Mattermost shows a token's value only at creation, so the caller must keep it.

        Args:
            description: Label shown in Mattermost's token list.

        Returns:
            The token secret. Never log it.

        Raises:
            SwitchUnavailable: Mattermost was unreachable or failed; safe to retry.
            SwitchRequestFailed: Enabling tokens or creating one was refused.
        """
        await self._send(
            "PUT",
            "/config/patch",
            "enabling access tokens",
            {"ServiceSettings": {"EnableUserAccessTokens": True}},
        )
        me = await self._send("GET", "/users/me", "reading the admin account")
        created = await self._send(
            "POST",
            f"/users/{me.json()['id']}/tokens",
            "creating an access token",
            {"description": description},
        )
        return str(created.json()["token"])

    async def aclose(self) -> None:
        """Close the pooled HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        """Return self for ``async with``."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the client when the block ends."""
        await self.aclose()

    async def _send(
        self, method: str, path: str, action: str, body: dict[str, object] | None = None
    ) -> httpx.Response:
        """Sign in on first use, then send one checked request."""
        await self._sign_in()
        return await send_checked(
            self._client, method, path, action=action, body=body, service=_SERVICE
        )

    async def _sign_in(self) -> None:
        """Log in once and keep the session token for every later request."""
        if self._signed_in:
            return
        response = await send_checked(
            self._client,
            "POST",
            "/users/login",
            action="signing in to Mattermost",
            body={"login_id": self._login, "password": self._password},
            service=_SERVICE,
        )
        self._client.headers["Authorization"] = f"Bearer {response.headers['Token']}"
        self._signed_in = True
