"""Command-line flags and environment settings for the ``register_agents`` command."""

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from heimdall.adapters.switch.agent.credentials import switch_api_url
from heimdall.shared.errors import ConfigInvalid

DEFAULT_SECRETS_FILE: Final = Path("secrets/switch-agents.env")


@dataclass(frozen=True)
class RunConfig:
    """Everything the command needs, read from the command line and the environment.

    Attributes:
        base_url: Switch API root.
        registration_token: Switch's bootstrap registration token; empty in addressing-only
            mode. Hidden from ``repr``.
        admin: Gateway admin ``(email, password)``, present only when addressing is set.
            Hidden from ``repr``.
        room_id: Switch id of the triage room, or None to leave addressing at Switch's default.
        overwrite: Replace existing agents, rotating their keys.
        addressing_only: Set addressing for agents already registered; no keys are touched.
        secrets_file: Where ids and keys are written.
    """

    base_url: str
    registration_token: str = field(repr=False)
    admin: tuple[str, str] | None = field(repr=False)
    room_id: str | None
    overwrite: bool
    addressing_only: bool
    secrets_file: Path


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Read the flags from the command line.

    Args:
        argv: Arguments after the program name.

    Returns:
        Namespace with ``overwrite``, ``addressing_only``, ``secrets_file`` and ``room_id``.
    """
    parser = argparse.ArgumentParser(prog="register_agents", description=__doc__)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace existing agents; rotates their keys and invalidates the old ones",
    )
    parser.add_argument(
        "--addressing-only",
        action="store_true",
        help="set addressing for agents in the secrets file; registers nothing, rotates nothing",
    )
    parser.add_argument("--secrets-file", type=Path, default=DEFAULT_SECRETS_FILE)
    parser.add_argument(
        "--room-id",
        type=_non_blank,
        default=None,
        help="Switch id of the triage room; only people in it may then @-mention the agents",
    )
    return parser.parse_args(list(argv))


def load_config(args: argparse.Namespace, environ: Mapping[str, str]) -> RunConfig:
    """Combine flags and environment into one validated config.

    Args:
        args: Parsed flags.
        environ: Process environment.

    Returns:
        The run config.

    Raises:
        ConfigInvalid: a required variable is unset, the Switch URL is unsafe, or
            ``--addressing-only`` was given without ``--room-id``. Errors name the variable only.
    """
    if args.addressing_only and not args.room_id:
        raise ConfigInvalid("--addressing-only needs --room-id")
    admin = None
    if args.room_id:
        admin = (
            _required(environ, "GATEWAY_ADMIN_EMAIL"),
            _required(environ, "GATEWAY_ADMIN_PASSWORD"),
        )
    token = "" if args.addressing_only else _required(environ, "AGENT_REGISTRATION_TOKEN")
    return RunConfig(
        base_url=switch_api_url(environ),
        registration_token=token,
        admin=admin,
        room_id=args.room_id,
        overwrite=args.overwrite,
        addressing_only=args.addressing_only,
        secrets_file=args.secrets_file,
    )


def _non_blank(value: str) -> str:
    """Argparse type: a trimmed, non-empty string."""
    trimmed = value.strip()
    if not trimmed:
        raise argparse.ArgumentTypeError("must not be blank")
    return trimmed


def _required(environ: Mapping[str, str], name: str) -> str:
    """Return a non-blank variable or fail naming it."""
    value = environ.get(name, "").strip()
    if not value:
        raise ConfigInvalid(f"{name} is not set")
    return value
