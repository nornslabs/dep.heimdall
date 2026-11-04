"""Shared request sending and error mapping for the provisioning clients."""

import httpx

from heimdall.shared.errors import SwitchRequestFailed, SwitchUnavailable

PROVISIONING_TIMEOUT_SECONDS = 15.0
_TOO_MANY_REQUESTS = 429
_SERVER_ERROR_FLOOR = 500


class SwitchRejected(SwitchRequestFailed):
    """Switch refused a request; ``status`` lets a caller treat one code specially.

    Attributes:
        status: HTTP status code of the refusal.
    """

    def __init__(self, message: str, *, status: int) -> None:
        """Keep the status beside the safe message."""
        super().__init__(message)
        self.status = status


async def send_checked(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    action: str,
    body: dict[str, object] | None = None,
    service: str = "Switch",
) -> httpx.Response:
    """Send one JSON request and map every failure to a Heimdall error.

    Messages name the action and status only. They never include the response body, because
    a rejected request can echo credentials back.

    Args:
        client: Client already carrying the base URL and credentials.
        method: HTTP method.
        path: Path under the base URL.
        action: Short phrase for messages, for example ``"registering huginn"``.
        body: JSON body, or None for a request without one.
        service: Name used in messages, ``"Switch"`` or ``"Mattermost"``.

    Returns:
        The successful response.

    Raises:
        SwitchUnavailable: Network failure, timeout, 429 or a 5xx; safe to retry later.
        SwitchRejected: The service refused it (a SwitchRequestFailed); retrying will not help.
    """
    try:
        response = await client.request(method, path, json=body)
    except httpx.HTTPError as error:
        raise SwitchUnavailable(
            f"{service} unreachable while {action}: {type(error).__name__}"
        ) from error
    if response.is_success:
        return response
    status = response.status_code
    if status >= _SERVER_ERROR_FLOOR or status == _TOO_MANY_REQUESTS:
        raise SwitchUnavailable(f"{service} returned {status} while {action}")
    raise SwitchRejected(f"{service} returned {status} while {action}", status=status)
