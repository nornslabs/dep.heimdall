"""Operator command: point one repository at the Switch room its triage is posted to."""

import argparse
import asyncio
import sys
from collections.abc import Sequence

from heimdall.adapters.db.engine import create_engine, make_session_factory
from heimdall.modules.tenancy.api import get_repo_id, get_tenant, link_repo_room
from heimdall.shared.ids import InstallationId
from heimdall.shared.settings import get_settings


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Read the installation id, repository name and room id from the command line.

    Args:
        argv: Arguments after the program name.

    Returns:
        Namespace with integer ``installation_id``, string ``repo_full_name`` and ``room_id``.
    """
    parser = argparse.ArgumentParser(prog="link_room", description=__doc__)
    parser.add_argument("installation_id", type=int, help="GitHub App installation id")
    parser.add_argument("repo_full_name", help="GitHub repository as owner/name")
    parser.add_argument("room_id", help="Switch channel id")
    return parser.parse_args(list(argv))


async def link(installation_id: int, repo_full_name: str, room_id: str) -> None:
    """Link the repo to the room using the process settings' database.

    Args:
        installation_id: GitHub App installation id.
        repo_full_name: Repository name as owner/name.
        room_id: Switch channel id.
    """
    engine = create_engine(get_settings().database_url.get_secret_value())
    try:
        sessions = make_session_factory(engine)
        ctx = await get_tenant(sessions, InstallationId(installation_id))
        repo_id = await get_repo_id(sessions, ctx, repo_full_name)
        await link_repo_room(sessions, ctx, repo_id, room_id)
    finally:
        await engine.dispose()


def main() -> None:
    """Run the command."""
    args = parse_args(sys.argv[1:])
    asyncio.run(link(args.installation_id, args.repo_full_name, args.room_id))
    sys.stdout.write(f"linked repo {args.repo_full_name} to room {args.room_id}\n")


if __name__ == "__main__":
    main()
