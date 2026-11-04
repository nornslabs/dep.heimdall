"""Command-line flags and environment settings for the ``setup`` command."""

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from apps.bridge._register_config import DEFAULT_SECRETS_FILE
from heimdall.adapters.switch.agent.credentials import switch_api_url
from heimdall.shared.errors import ConfigInvalid

DEFAULT_ENV_FILE: Final = Path(".env")
DEFAULT_CHANNEL: Final = "triage"
DEFAULT_TEAM: Final = "heimdall"
DEFAULT_MATTERMOST_PORT: Final = "8065"
TOKEN_VARIABLE: Final = "HEIMDALL_SWITCH_TOKEN"


@dataclass(frozen=True)
class SetupConfig:
    """Everything the command needs, read from the command line and the environment.

    Attributes:
        mattermost_url: Mattermost root as this machine reaches it.
        switch_url: Switch API root.
        team: Mattermost team URL slug.
        channel: Name of the triage channel to create or reuse.
        mattermost_admin: Mattermost admin ``(username, password)``. Hidden from ``repr``.
        maintainer: Mattermost username of the human who will ping the agents, if any.
        existing_token: ``HEIMDALL_SWITCH_TOKEN`` already in ``.env``, kept while it works.
            Hidden from ``repr``.
        env_file: File that receives the values.
        secrets_file: Where agent ids and keys are kept.
        show_token: Print the token in the summary.
    """

    mattermost_url: str
    switch_url: str
    team: str
    channel: str
    mattermost_admin: tuple[str, str] = field(repr=False)
    maintainer: str | None
    existing_token: str | None = field(repr=False)
    env_file: Path
    secrets_file: Path
    show_token: bool


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Read the flags from the command line.

    Args:
        argv: Arguments after the program name.

    Returns:
        Namespace with ``mattermost_url``, ``channel``, ``env_file``, ``secrets_file`` and
        ``show_token``.
    """
    parser = argparse.ArgumentParser(prog="setup", description=__doc__)
    parser.add_argument(
        "--mattermost-url",
        default=None,
        help="Mattermost root; defaults to http://localhost:$MATTERMOST_PORT",
    )
    parser.add_argument("--channel", default=DEFAULT_CHANNEL, help="triage channel name")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--secrets-file", type=Path, default=DEFAULT_SECRETS_FILE)
    parser.add_argument(
        "--print",
        dest="show_token",
        action="store_true",
        help="also print HEIMDALL_SWITCH_TOKEN; by default it is only written to the env file",
    )
    return parser.parse_args(list(argv))


def load_config(args: argparse.Namespace, environ: Mapping[str, str]) -> SetupConfig:
    """Combine flags and environment into one validated config.

    Args:
        args: Parsed flags.
        environ: Process environment, already merged over the env file.

    Returns:
        The setup config.

    Raises:
        ConfigInvalid: a required variable is unset or the Switch URL is unsafe. Errors name
            the variable only.
    """
    port = environ.get("MATTERMOST_PORT", "").strip() or DEFAULT_MATTERMOST_PORT
    return SetupConfig(
        mattermost_url=args.mattermost_url or f"http://localhost:{port}",
        switch_url=switch_api_url(environ),
        team=environ.get("MATTERMOST_TEAM_NAME", "").strip() or DEFAULT_TEAM,
        channel=args.channel,
        mattermost_admin=(
            _required(environ, "MATTERMOST_ADMIN_USER"),
            _required(environ, "MATTERMOST_ADMIN_PASSWORD"),
        ),
        maintainer=environ.get("MATTERMOST_USER", "").strip() or None,
        existing_token=environ.get(TOKEN_VARIABLE, "").strip() or None,
        env_file=args.env_file,
        secrets_file=args.secrets_file,
        show_token=args.show_token,
    )


def _required(environ: Mapping[str, str], name: str) -> str:
    """Return a non-blank variable or fail naming it."""
    value = environ.get(name, "").strip()
    if not value:
        raise ConfigInvalid(f"{name} is not set")
    return value
