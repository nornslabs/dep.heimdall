"""Operator command: prepare Mattermost and Switch, then write the values the stack needs.

Run once after ``docker compose up -d`` (infrastructure only) and before starting the app
containers: ``uv run python -m apps.bridge.setup``. It creates the triage channel and an access
token, registers the agents, finds Switch's room id and writes ``HEIMDALL_SWITCH_TOKEN``,
``HEIMDALL_DEFAULT_SWITCH_ROOM_ID`` and ``HEIMDALL_DEFAULT_SWITCH_AGENT_ROOM_ID`` to ``.env``.
Safe to re-run: it reuses what exists and rotates no key.
"""

import asyncio
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Final

from apps.bridge._env_file import merge_env_file, read_env_file
from apps.bridge._setup_config import (
    TOKEN_VARIABLE,
    SetupConfig,
    load_config,
    parse_args,
)
from apps.bridge._setup_steps import (
    Sleep,
    add_channel_members,
    prepare_mattermost,
    register_missing_agents,
    say,
    set_addressing,
    wait_for_agent_room,
    wait_until_healthy,
)
from heimdall.shared.errors import ConfigInvalid, SwitchRequestFailed, SwitchUnavailable

__all__ = ["main", "parse_args", "run"]

EXIT_OK: Final = 0
EXIT_INCOMPLETE: Final = 1
EXIT_BAD_CONFIG: Final = 2
ROOM_VARIABLE: Final = "HEIMDALL_DEFAULT_SWITCH_ROOM_ID"
AGENT_ROOM_VARIABLE: Final = "HEIMDALL_DEFAULT_SWITCH_AGENT_ROOM_ID"


async def run(argv: Sequence[str], environ: Mapping[str, str], *, sleep: Sleep) -> int:
    """Run every setup step in order and stop at the first one that cannot finish.

    The token is written as soon as it exists, because Mattermost shows it only once.

    Args:
        argv: Arguments after the program name.
        environ: Process environment; the env file fills in what it does not set.
        sleep: Pause used while waiting for services; injected so tests do not wait.

    Returns:
        0 when every value is written, 1 when a step failed or is not ready yet.

    Raises:
        ConfigInvalid: a required variable is unset.
    """
    args = parse_args(argv)
    merged = {**read_env_file(args.env_file), **environ}
    config = load_config(args, merged)
    try:
        return await _run_steps(config, merged, sleep)
    except (SwitchUnavailable, SwitchRequestFailed) as error:
        say(f"setup stopped: {error}")
        return EXIT_INCOMPLETE


async def _run_steps(config: SetupConfig, environ: Mapping[str, str], sleep: Sleep) -> int:
    """Health checks, Mattermost, agents, room, addressing, then the summary."""
    if not await _services_healthy(config, sleep):
        return EXIT_INCOMPLETE
    channel_id, token = await prepare_mattermost(config)
    merge_env_file(config.env_file, {TOKEN_VARIABLE: token, ROOM_VARIABLE: channel_id})
    if await register_missing_agents(config, environ) != 0:
        return EXIT_INCOMPLETE
    await add_channel_members(config, channel_id)
    agent_room_id = await wait_for_agent_room(config, environ, sleep)
    if agent_room_id is None:
        return EXIT_INCOMPLETE
    merge_env_file(config.env_file, {AGENT_ROOM_VARIABLE: agent_room_id})
    if await set_addressing(config, environ, agent_room_id) != 0:
        return EXIT_INCOMPLETE
    _summarise(config, token, channel_id, agent_room_id)
    return EXIT_OK


async def _services_healthy(config: SetupConfig, sleep: Sleep) -> bool:
    """Wait for Switch, then Mattermost."""
    switch_ok = await wait_until_healthy("Switch", f"{config.switch_url}/health", sleep)
    if not switch_ok:
        return False
    return await wait_until_healthy(
        "Mattermost", f"{config.mattermost_url}/api/v4/system/ping", sleep
    )


def _summarise(config: SetupConfig, token: str, channel_id: str, agent_room_id: str) -> None:
    """Print the three values; the token only with ``--print``."""
    shown_token = token if config.show_token else f"(saved to {config.env_file}; --print shows it)"
    say(f"\nWritten to {config.env_file}:")
    say(f"  {TOKEN_VARIABLE}={shown_token}")
    say(f"  {ROOM_VARIABLE}={channel_id}")
    say(f"  {AGENT_ROOM_VARIABLE}={agent_room_id}")
    say("\nNext: docker compose --profile app up -d")


def main() -> None:
    """Run the command and exit with its status."""
    try:
        code = asyncio.run(run(sys.argv[1:], os.environ, sleep=asyncio.sleep))
    except ConfigInvalid as error:
        sys.stderr.write(f"setup: {error}\n")
        raise SystemExit(EXIT_BAD_CONFIG) from error
    raise SystemExit(code)


if __name__ == "__main__":
    main()
