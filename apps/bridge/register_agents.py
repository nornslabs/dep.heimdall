"""Operator command: register Huginn, Loki and Heimdall with Switch and save their API keys.

Run once after Switch is up: ``uv run python -m apps.bridge.register_agents --room-id <id>``.
Switch shows each key only once, so the keys go straight to a private file and are never
printed or logged. If only the addressing step failed, ``--addressing-only`` retries it
without touching any key.
"""

import asyncio
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Final

from apps.bridge._env_file import check_writable, merge_env_file, read_env_file
from apps.bridge._register_config import RunConfig, load_config, parse_args
from heimdall.adapters.switch.agent.credentials import credential_names
from heimdall.adapters.switch.provisioning import (
    AGENT_SPECS,
    AgentRegistrar,
    AgentSpec,
    SwitchAdmin,
)
from heimdall.shared.errors import (
    ConfigInvalid,
    SwitchAgentExists,
    SwitchRequestFailed,
    SwitchUnavailable,
)

__all__ = ["load_config", "main", "parse_args", "run"]

EXIT_OK: Final = 0
EXIT_INCOMPLETE: Final = 1
EXIT_BAD_CONFIG: Final = 2


class _CredentialsNotSaved(Exception):  # noqa: N818 - control flow, not an error type
    """A key was issued but could not be written; the run must stop."""


async def run(argv: Sequence[str], environ: Mapping[str, str]) -> int:
    """Register every agent, save its credentials and report one line per agent.

    A failure for one agent does not stop the others, except when a key cannot be saved: then
    the run stops, because every further key would be lost the same way. Each key is written
    before the next agent is tried.

    Args:
        argv: Arguments after the program name.
        environ: Process environment.

    Returns:
        0 when every agent succeeded, 1 when any was skipped or failed, 2 when the admin
        sign-in was refused (nothing was changed).

    Raises:
        ConfigInvalid: a required variable is unset or the secrets file cannot be written.
    """
    config = load_config(parse_args(argv), environ)
    if not config.addressing_only:
        _require_writable(config)
    admin = _admin_for(config)
    try:
        if admin is not None and not await _sign_in(admin):
            return EXIT_BAD_CONFIG
        succeeded = (
            await _addressing_only(admin, config)
            if config.addressing_only
            else (await _register_all(admin, config))
        )
    finally:
        if admin is not None:
            await admin.aclose()
    if config.room_id is None:
        _say(
            "addressing left at Switch's default; pass --room-id to restrict it to the triage room"
        )
    return EXIT_OK if succeeded == len(AGENT_SPECS) else EXIT_INCOMPLETE


def _require_writable(config: RunConfig) -> None:
    """Fail before any key is issued if the secrets file could not be written."""
    try:
        check_writable(config.secrets_file)
    except OSError as error:
        raise ConfigInvalid(f"cannot write {config.secrets_file}: {error.strerror}") from error


def _admin_for(config: RunConfig) -> SwitchAdmin | None:
    """Build the admin client only when addressing will be set."""
    if config.admin is None:
        return None
    email, password = config.admin
    return SwitchAdmin(config.base_url, email, password)


async def _sign_in(admin: SwitchAdmin) -> bool:
    """Sign in once, before any agent is changed. Return False when it fails."""
    try:
        await admin.sign_in()
    except (SwitchUnavailable, SwitchRequestFailed) as error:
        _say(f"cannot sign in to Switch as admin ({error}); nothing was changed")
        return False
    return True


async def _register_all(admin: SwitchAdmin | None, config: RunConfig) -> int:
    """Register each agent in turn. Return how many fully succeeded."""
    succeeded = 0
    async with AgentRegistrar(config.base_url, config.registration_token) as registrar:
        for spec in AGENT_SPECS:
            try:
                succeeded += await _register_one(registrar, admin, spec, config)
            except _CredentialsNotSaved:
                break
    return succeeded


async def _register_one(
    registrar: AgentRegistrar, admin: SwitchAdmin | None, spec: AgentSpec, config: RunConfig
) -> int:
    """Register one agent, save its credentials, set addressing. Return 1 on full success.

    Raises:
        _CredentialsNotSaved: Switch issued a key that could not be written to the file.
    """
    try:
        registration = await registrar.register(spec, overwrite=config.overwrite)
    except SwitchAgentExists:
        _say(
            f"{spec.name}: already registered; pass --overwrite to rotate its key "
            "(this invalidates the old one), or --addressing-only to just set addressing"
        )
        return 0
    except (SwitchUnavailable, SwitchRequestFailed) as error:
        _say(f"{spec.name}: not registered ({error})")
        return 0
    _save(spec, registration.agent_id, registration.api_key, config)
    if admin is None or config.room_id is None:
        _say(f"{spec.name}: registered as {registration.agent_id}; credentials saved")
        return 1
    return await _set_addressing(admin, spec, registration.agent_id, config.room_id, "registered; ")


def _save(spec: AgentSpec, agent_id: str, api_key: str, config: RunConfig) -> None:
    """Write the agent's id and key, or report loudly that the key is now lost."""
    id_name, key_name = credential_names(spec.name)
    try:
        merge_env_file(config.secrets_file, {id_name: agent_id, key_name: api_key})
    except (OSError, ValueError) as error:
        _say(
            f"{spec.name}: registered as {agent_id} but its key was NOT saved "
            f"({type(error).__name__}); Switch shows a key once, so fix the file and re-run "
            "with --overwrite. Stopping."
        )
        raise _CredentialsNotSaved from error


async def _addressing_only(admin: SwitchAdmin | None, config: RunConfig) -> int:
    """Set addressing for agents whose ids are already in the secrets file."""
    if admin is None or config.room_id is None:
        return 0
    saved = read_env_file(config.secrets_file)
    succeeded = 0
    for spec in AGENT_SPECS:
        agent_id = saved.get(credential_names(spec.name)[0])
        if not agent_id:
            _say(f"{spec.name}: no saved id in {config.secrets_file}; register it first")
            continue
        succeeded += await _set_addressing(admin, spec, agent_id, config.room_id, "")
    return succeeded


async def _set_addressing(
    admin: SwitchAdmin, spec: AgentSpec, agent_id: str, room_id: str, done: str
) -> int:
    """Restrict who may address the agent. Return 1 on success."""
    try:
        await admin.set_addressing(agent_id=agent_id, room_id=room_id)
    except (SwitchUnavailable, SwitchRequestFailed) as error:
        _say(
            f"{spec.name}: {done}addressing failed ({error}); "
            "retry with --addressing-only (no key is rotated)"
        )
        return 0
    _say(f"{spec.name}: {done}addressing set for {agent_id}")
    return 1


def _say(line: str) -> None:
    """Print one line for the operator. Never pass a key or token."""
    sys.stdout.write(f"{line}\n")


def main() -> None:
    """Run the command and exit with its status."""
    try:
        code = asyncio.run(run(sys.argv[1:], os.environ))
    except ConfigInvalid as error:
        sys.stderr.write(f"register_agents: {error}\n")
        raise SystemExit(EXIT_BAD_CONFIG) from error
    raise SystemExit(code)


if __name__ == "__main__":
    main()
