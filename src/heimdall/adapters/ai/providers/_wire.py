"""HTTP and JSON helpers shared by providers: status mapping and safe JSON narrowing."""

import json
from collections.abc import Mapping
from typing import cast

import httpx

from heimdall.shared.errors import AiBadResponse, AiRequestFailed, AiUnavailable

TOO_MANY_REQUESTS = 429
FIRST_SERVER_ERROR = 500
FIRST_CLIENT_ERROR = 400


async def post_json(
    http: httpx.AsyncClient,
    url: str,
    headers: Mapping[str, str],
    body: Mapping[str, object],
) -> dict[str, object]:
    """POST a JSON body and return the decoded JSON object, mapping failures to AI errors.

    Args:
        http: Client to send with.
        url: Full endpoint URL.
        headers: Auth and version headers; never logged.
        body: Request payload.

    Returns:
        The response body as a JSON object.

    Raises:
        AiUnavailable: network error, timeout, 429 or 5xx.
        AiRequestFailed: any other 4xx.
        AiBadResponse: the body is not a JSON object.
    """
    try:
        response = await http.post(url, headers=dict(headers), json=body)
    except httpx.TransportError as error:
        raise AiUnavailable(f"model provider unreachable: {type(error).__name__}") from error
    _raise_for_status(response)
    try:
        decoded: object = response.json()
    except json.JSONDecodeError as error:
        raise AiBadResponse("model provider returned invalid JSON") from error
    payload = as_object(decoded)
    if payload is None:
        raise AiBadResponse("model provider returned JSON that is not an object")
    return payload


def _raise_for_status(response: httpx.Response) -> None:
    """Split retryable outages from requests that cannot succeed unchanged."""
    status = response.status_code
    if status == TOO_MANY_REQUESTS or status >= FIRST_SERVER_ERROR:
        raise AiUnavailable(
            f"model provider returned {status}",
            retry_after=_retry_after(response.headers.get("Retry-After")),
        )
    if status >= FIRST_CLIENT_ERROR:
        raise AiRequestFailed(f"model provider returned {status}")


def _retry_after(header: str | None) -> float | None:
    """Read a Retry-After given in seconds; HTTP-date values fall back to the backoff."""
    if header is None:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def as_object(value: object) -> dict[str, object] | None:
    """Return a JSON object with string keys, or None for any other value."""
    if not isinstance(value, dict):
        return None
    return {str(key): item for key, item in cast(dict[object, object], value).items()}


def as_list(value: object) -> list[object]:
    """Return a JSON array, or an empty list for any other value."""
    if not isinstance(value, list):
        return []
    return list(cast(list[object], value))


def token_count(usage: Mapping[str, object] | None, name: str) -> int:
    """Read one token counter, counting a missing or malformed value as zero."""
    if usage is None:
        return 0
    value = usage.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def reply_model(payload: Mapping[str, object], requested: str) -> str:
    """Prefer the model the provider says answered, else the one we asked for."""
    model = payload.get("model")
    return model if isinstance(model, str) and model else requested
