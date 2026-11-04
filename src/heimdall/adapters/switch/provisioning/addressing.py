"""Set who may @-mention an agent, through the Gateway API as a signed-in admin."""

from types import TracebackType
from typing import Final, Self

import httpx

from heimdall.adapters.switch.provisioning._http import PROVISIONING_TIMEOUT_SECONDS, send_checked

# Switch serves the user-facing Gateway API under /gateway; agent registration is at the root.
GATEWAY_PREFIX: Final = "/gateway"
_LOGIN_PATH: Final = "/auth/login"
_ANY: Final = "*"
_MATTERMOST_BRIDGE: Final = "mattermost"


def triage_room_policy(room_id: str) -> dict[str, object]:
    """The narrowest addressing policy that lets maintainers mention an agent.

    One allow-rule: people in the triage room may address the agent. No other agent can, the
    Switch platform cannot, and nobody outside the room can. Switch denies everything a rule
    does not allow once any rule exists.

    Args:
        room_id: Switch id of the triage room.

    Returns:
        A policy in the shape ``PUT /agents/{id}/addressing-policy`` expects.
    """
    return {
        "rules": [
            {
                "rooms": [room_id],
                "room_groups": _ANY,
                "users": _ANY,
                "agents": [],
                "owner": False,
                "owner_agents": False,
                "platform": False,
            }
        ]
    }


class SwitchAdmin:
    """Signs in once as a Switch admin, then changes agent settings that need a user."""

    def __init__(
        self,
        base_url: str,
        email: str,
        password: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Create the HTTP client; its cookie jar keeps the session after login.

        Args:
            base_url: Switch API root, for example ``http://switch:8000``.
            email: Gateway admin email.
            password: Gateway admin password. Never logged.
            transport: Optional replacement transport for tests.
        """
        self._email = email
        self._password = password
        self._signed_in = False
        self._client = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}{GATEWAY_PREFIX}",
            timeout=PROVISIONING_TIMEOUT_SECONDS,
            transport=transport,
        )

    async def set_addressing(self, *, agent_id: str, room_id: str) -> None:
        """Restrict an agent to being addressed by people in the triage room.

        Args:
            agent_id: Switch id returned at registration.
            room_id: Switch id of the triage room.

        Raises:
            SwitchUnavailable: Switch was unreachable or failed; safe to retry.
            SwitchRequestFailed: Login or the update was refused.
        """
        await self.sign_in()
        await send_checked(
            self._client,
            "PUT",
            f"/agents/{agent_id}/addressing-policy",
            action=f"setting addressing for agent {agent_id}",
            body={"policy": triage_room_policy(room_id)},
        )

    async def find_room_id(self, *, channel_slug: str) -> str | None:
        """Find the Switch room that Switch bridged from a Mattermost channel.

        Switch names bridged rooms by display name, so the match is on the channel's URL slug,
        which Heimdall controls.

        Args:
            channel_slug: Mattermost channel name, for example ``triage``.

        Returns:
            Switch's own room UUID, or None while the channel has not been bridged yet.

        Raises:
            SwitchUnavailable: Switch was unreachable or failed; safe to retry.
            SwitchRequestFailed: Login or the listing was refused.
        """
        await self.sign_in()
        response = await send_checked(
            self._client, "GET", "/rooms", action=f"listing rooms for {channel_slug}"
        )
        suffix = f"/channels/{channel_slug}"
        for room in response.json():
            if room.get("bridge_type") == _MATTERMOST_BRIDGE and str(
                room.get("external_channel_url", "")
            ).endswith(suffix):
                return str(room["id"])
        return None

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

    async def sign_in(self) -> None:
        """Log in now, so bad admin credentials surface before any agent is changed.

        A second call, and every later request, reuses the session cookie.

        Raises:
            SwitchUnavailable: Switch was unreachable or failed; safe to retry.
            SwitchRequestFailed: Switch refused the email or password.
        """
        if self._signed_in:
            return
        await send_checked(
            self._client,
            "POST",
            _LOGIN_PATH,
            action="signing in to Switch",
            body={"email": self._email, "password": self._password},
        )
        self._signed_in = True
