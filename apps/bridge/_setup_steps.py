"""The individual steps of the ``setup`` command, each small enough to read on its own."""

import sys
from collections.abc import Awaitable, Callable, Mapping
from typing import Final

import httpx

from apps.bridge import register_agents
from apps.bridge._env_file import read_env_file
from apps.bridge._setup_config import SetupConfig
from heimdall.adapters.switch.agent.credentials import credential_names
from heimdall.adapters.switch.provisioning import AGENT_SPECS, MattermostAdmin, SwitchAdmin

Sleep = Callable[[float], Awaitable[None]]

POLL_ATTEMPTS: Final = 30
POLL_DELAY_SECONDS: Final = 2.0
HEALTH_TIMEOUT_SECONDS: Final = 5.0
TOKEN_DESCRIPTION: Final = "heimdall"


def say(line: str) -> None:
    """Print one line for the operator. Never pass a key or token."""
    sys.stdout.write(f"{line}\n")


async def wait_until_healthy(name: str, url: str, sleep: Sleep) -> bool:
    """Poll a service's health URL until it answers 2xx.

    Args:
        name: Service name for messages.
        url: Full health URL.
        sleep: Pause between attempts; injected so tests do not wait.

    Returns:
        True once healthy, False after ``POLL_ATTEMPTS`` tries.
    """
    async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT_SECONDS) as client:
        for _ in range(POLL_ATTEMPTS):
            try:
                if (await client.get(url)).is_success:
                    return True
            except httpx.HTTPError:
                pass
            await sleep(POLL_DELAY_SECONDS)
    say(f"{name} did not become healthy at {url}; is `docker compose up -d` running?")
    return False


async def prepare_mattermost(config: SetupConfig) -> tuple[str, str]:
    """Create the triage channel and an access token, reusing a token that still works.

    Mattermost shows a token only when it is created, so a working token already in the env
    file is kept instead of replaced.

    Args:
        config: Setup config.

    Returns:
        ``(channel id, token)``. The token is a secret.

    Raises:
        SwitchUnavailable: Mattermost was unreachable or failed.
        SwitchRequestFailed: A request was refused.
    """
    user, password = config.mattermost_admin
    async with MattermostAdmin(config.mattermost_url, user, password) as admin:
        team_id = await admin.team_id(config.team)
        channel_id = await admin.ensure_channel(team_id, config.channel)
        token = await _usable_token(config, admin)
    say(f"mattermost: channel '{config.channel}' ready; access token ready")
    return channel_id, token


async def _usable_token(config: SetupConfig, admin: MattermostAdmin) -> str:
    """Keep the saved token if Mattermost accepts it, else create a new one."""
    saved = config.existing_token
    if saved and await MattermostAdmin.token_works(config.mattermost_url, saved):
        return saved
    return await admin.create_access_token(TOKEN_DESCRIPTION)


async def register_missing_agents(config: SetupConfig, environ: Mapping[str, str]) -> int:
    """Register the agents unless all three are already saved; never rotates a key.

    Args:
        config: Setup config.
        environ: Environment for ``register_agents``.

    Returns:
        ``register_agents`` exit code; 0 also when nothing needed registering.
    """
    saved = read_env_file(config.secrets_file)
    if all(saved.get(credential_names(spec.name)[0]) for spec in AGENT_SPECS):
        say("agents: already registered; keys left as they are")
        return register_agents.EXIT_OK
    return await register_agents.run(["--secrets-file", str(config.secrets_file)], environ)


async def add_channel_members(config: SetupConfig, channel_id: str) -> None:
    """Put the three agent bots, and the maintainer if configured, in the triage channel.

    Raises:
        SwitchUnavailable: Mattermost was unreachable or failed.
        SwitchRequestFailed: A user is missing or a request was refused.
    """
    usernames = [spec.name for spec in AGENT_SPECS]
    if config.maintainer:
        usernames.append(config.maintainer)
    user, password = config.mattermost_admin
    async with MattermostAdmin(config.mattermost_url, user, password) as admin:
        await admin.add_members(channel_id, usernames)
    say(f"mattermost: {', '.join(usernames)} are in '{config.channel}'")


async def wait_for_agent_room(
    config: SetupConfig, environ: Mapping[str, str], sleep: Sleep
) -> str | None:
    """Wait for Switch to bridge the channel, then return Switch's own room UUID.

    Args:
        config: Setup config.
        environ: Supplies the Gateway admin login.
        sleep: Pause between attempts.

    Returns:
        The room UUID, or None if the channel is still not bridged after the last attempt.

    Raises:
        SwitchUnavailable: Switch was unreachable or failed.
        SwitchRequestFailed: Admin sign-in or the listing was refused.
    """
    email = environ.get("GATEWAY_ADMIN_EMAIL", "")
    password = environ.get("GATEWAY_ADMIN_PASSWORD", "")
    async with SwitchAdmin(config.switch_url, email, password) as admin:
        for _ in range(POLL_ATTEMPTS):
            room_id = await admin.find_room_id(channel_slug=config.channel)
            if room_id is not None:
                return room_id
            await sleep(POLL_DELAY_SECONDS)
    say(f"switch: channel '{config.channel}' is not bridged into Switch yet; re-run setup shortly")
    return None


async def set_addressing(config: SetupConfig, environ: Mapping[str, str], room_id: str) -> int:
    """Restrict who may mention the agents to people in the triage room.

    Returns:
        ``register_agents`` exit code.
    """
    return await register_agents.run(
        ["--addressing-only", "--room-id", room_id, "--secrets-file", str(config.secrets_file)],
        environ,
    )
