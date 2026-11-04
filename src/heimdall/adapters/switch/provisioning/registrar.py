"""Register an agent with Switch (``POST /agents``) and receive its one-time API key."""

from dataclasses import dataclass, field
from types import TracebackType
from typing import Final, Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from heimdall.adapters.switch.provisioning._http import (
    PROVISIONING_TIMEOUT_SECONDS,
    SwitchRejected,
    send_checked,
)
from heimdall.adapters.switch.provisioning.specs import AgentSpec
from heimdall.shared.errors import SwitchAgentExists, SwitchRequestFailed

CONNECTOR_TYPE: Final = "heimdall"
# Each agent holds its own WebSocket open, so Switch should treat it as always connected.
CONNECTION_MODEL: Final = "always_on"
_CONFLICT: Final = 409
_AGENT_ID_PATTERN: Final = r"^[A-Za-z0-9_-]{1,64}$"


@dataclass(frozen=True)
class AgentRegistration:
    """The identity Switch issued for one agent.

    Attributes:
        agent_id: Switch's id for the agent.
        api_key: Bearer key for the agent's connection. Shown by Switch only once; hidden
            from ``repr`` so it cannot reach a log by accident.
    """

    agent_id: str
    api_key: str = field(repr=False)


class _RegisterResponse(BaseModel):
    """The two fields of Switch's registration reply that Heimdall uses."""

    model_config = ConfigDict(extra="ignore")

    # The id goes into URL paths later, so only characters that cannot alter a path are allowed.
    id: str = Field(pattern=_AGENT_ID_PATTERN)
    api_key: SecretStr


class AgentRegistrar:
    """Registers agents using Switch's bootstrap registration token."""

    def __init__(
        self,
        base_url: str,
        registration_token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Create the HTTP client; ``transport`` lets tests substitute one.

        Args:
            base_url: Switch API root, for example ``http://switch:8000``.
            registration_token: The server's ``AGENT_REGISTRATION_TOKEN``. Never logged.
            transport: Optional replacement transport for tests.
        """
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {registration_token}"},
            timeout=PROVISIONING_TIMEOUT_SECONDS,
            transport=transport,
        )

    async def register(self, spec: AgentSpec, *, overwrite: bool = False) -> AgentRegistration:
        """Register one agent so Switch gives it a bot account and an API key.

        Args:
            spec: Name and description of the agent.
            overwrite: Replace an existing agent of the same name. This rotates its key and
                invalidates the old one.

        Returns:
            The new agent id and its API key.

        Raises:
            SwitchAgentExists: An agent with this name exists and ``overwrite`` was false.
            SwitchUnavailable: Switch was unreachable or failed; safe to retry.
            SwitchRequestFailed: Switch refused the request or sent an unreadable reply.
        """
        action = f"registering {spec.name}"
        try:
            response = await send_checked(
                self._client, "POST", "/agents", action=action, body=_request_body(spec, overwrite)
            )
        except SwitchRejected as error:
            if error.status == _CONFLICT:
                raise SwitchAgentExists(f"agent {spec.name!r} is already registered") from error
            raise
        return _parse_registration(response, action)

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


def _request_body(spec: AgentSpec, overwrite: bool) -> dict[str, object]:
    """Build the ``POST /agents`` body: message exchange only, no mediation or reporting."""
    return {
        "name": spec.name,
        "description": spec.description,
        "connector_type": CONNECTOR_TYPE,
        "integration_profile": {
            "connection_model": CONNECTION_MODEL,
            "message_exchange": True,
            "pre_invocation_mediation": [],
            "post_invocation_mediation": [],
            "event_reporting": [],
        },
        "overwrite": overwrite,
    }


def _parse_registration(response: httpx.Response, action: str) -> AgentRegistration:
    """Read id and key from the reply, never echoing its body in an error."""
    try:
        parsed = _RegisterResponse.model_validate_json(response.content)
    except ValidationError as error:
        raise SwitchRequestFailed(
            f"Switch sent an unreadable reply while {action}; the agent may be registered, "
            "so re-run with --overwrite to get a key"
        ) from error
    return AgentRegistration(agent_id=parsed.id, api_key=parsed.api_key.get_secret_value())
