"""Read one agent's Switch id and API key from ``SWITCH_<AGENT>_*`` variables."""

import os
from collections.abc import Mapping
from urllib.parse import urlsplit

from heimdall.adapters.switch.agent.config import AgentConnectionConfig
from heimdall.shared.errors import ConfigInvalid

ENV_PREFIX = "SWITCH"
DEFAULT_SWITCH_API_URL = "http://localhost:8000"
_ALLOWED_SCHEMES = ("http", "https")
_LOOPBACK_HOSTS = ("127.0.0.1", "::1")


def switch_api_url(environ: Mapping[str, str] | None = None) -> str:
    """Switch API root from ``SWITCH_API_URL``, without a trailing slash.

    Keys, the registration token and the admin password travel to this URL, so cleartext
    ``http`` is accepted only for this machine or a compose service name (a host with no dot).
    Anything else must be ``https``.

    Args:
        environ: Variables to read; the process environment when omitted.

    Returns:
        The configured URL, or the local default when unset or blank.

    Raises:
        ConfigInvalid: the URL has credentials in it, is not http(s), has no host, or sends
            secrets in cleartext to a remote host. The error never repeats the URL.
    """
    env = os.environ if environ is None else environ
    value = env.get("SWITCH_API_URL", "").strip().rstrip("/")
    if not value:
        return DEFAULT_SWITCH_API_URL
    _require_safe_url(value)
    return value


def _require_safe_url(value: str) -> None:
    """Fail unless ``value`` is an http(s) URL with a host, no userinfo and a safe transport."""
    parts = urlsplit(value)
    host = parts.hostname
    if parts.scheme not in _ALLOWED_SCHEMES or not host:
        raise ConfigInvalid("SWITCH_API_URL must be an http(s) URL with a host")
    if parts.username is not None or parts.password is not None:
        raise ConfigInvalid("SWITCH_API_URL must not contain credentials")
    if parts.scheme == "http" and "." in host and host not in _LOOPBACK_HOSTS:
        raise ConfigInvalid("SWITCH_API_URL must use https for a remote host")


def credential_names(agent: str) -> tuple[str, str]:
    """Names of the two variables that hold an agent's Switch identity.

    Args:
        agent: Agent name such as ``huginn``; upper-cased into the names.

    Returns:
        ``(id variable, key variable)``, for example ``SWITCH_HUGINN_AGENT_ID`` and
        ``SWITCH_HUGINN_API_KEY``.
    """
    scope = agent.upper()
    return f"{ENV_PREFIX}_{scope}_AGENT_ID", f"{ENV_PREFIX}_{scope}_API_KEY"


def load_agent_credentials(
    agent: str,
    base_url: str,
    *,
    rooms: tuple[str, ...],
    environ: Mapping[str, str] | None = None,
) -> AgentConnectionConfig:
    """Build the agent's connection settings from its registered id and key.

    Args:
        agent: Agent name such as ``loki``.
        base_url: Switch API root.
        rooms: Rooms to claim when the socket opens.
        environ: Variables to read; the process environment when omitted.

    Returns:
        Connection settings with the key hidden from ``repr``.

    Raises:
        ConfigInvalid: either variable is unset or blank. The error names the variable, never
            a value.
    """
    env = os.environ if environ is None else environ
    id_name, key_name = credential_names(agent)
    agent_id = _required(env, id_name)
    api_key = _required(env, key_name)
    return AgentConnectionConfig(base_url=base_url, agent_id=agent_id, api_key=api_key, rooms=rooms)


def _required(env: Mapping[str, str], name: str) -> str:
    """Return the trimmed value, treating a blank one as unset."""
    value = env.get(name, "").strip()
    if not value:
        raise ConfigInvalid(f"{name} is not set")
    return value
