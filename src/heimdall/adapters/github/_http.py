"""One typed doorway for GET requests, so githubkit's loosely typed ``arequest`` stays here."""

from collections.abc import Mapping
from dataclasses import dataclass

from githubkit import AppInstallationAuthStrategy, GitHub

QueryValue = str | int


@dataclass(frozen=True)
class Reply:
    """A GitHub response: parsed JSON (when the body is JSON) and the raw text."""

    payload: object
    text: str


async def get(
    client: GitHub[AppInstallationAuthStrategy],
    url: str,
    *,
    params: Mapping[str, QueryValue] | None = None,
    accept: str | None = None,
) -> Reply:
    """Send an authenticated GET and return its body.

    Args:
        client: The installation's githubkit client.
        url: API path, for example ``/repos/o/r/pulls/1``.
        params: Query string values.
        accept: Media type to ask for; JSON when omitted.

    Returns:
        The reply; ``payload`` is ``None`` for non-JSON bodies such as diffs.

    Raises:
        githubkit.exception.RequestError: on any HTTP or network failure (mapped by callers).
    """
    headers = {"Accept": accept} if accept else None
    response = await client.arequest(  # pyright: ignore[reportUnknownMemberType]
        "GET", url, params=dict(params or {}), headers=headers
    )
    if accept is not None:
        return Reply(payload=None, text=response.text)
    return Reply(payload=response.json(), text=response.text)


async def get_bytes(
    client: GitHub[AppInstallationAuthStrategy],
    url: str,
) -> bytes:
    """GET one bounded binary response through the authenticated GitHub client.

    Args:
        client: Installation-scoped GitHub client.
        url: GitHub API path to fetch.

    Returns:
        Response body bytes.
    """
    response = await client.arequest(  # pyright: ignore[reportUnknownMemberType]
        "GET", url, response_model=bytes
    )
    return response.content
