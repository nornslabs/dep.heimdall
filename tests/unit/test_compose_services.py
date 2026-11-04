"""The compose file must not start services that exit on their own and restart forever."""

from pathlib import Path
from typing import Any

import yaml

COMPOSE_FILE = Path(__file__).resolve().parents[2] / "docker-compose.yml"
MCP_PROFILE = "mcp"


def _services() -> dict[str, dict[str, Any]]:
    """Parse the compose file's services."""
    document = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    return document["services"]


def test_mcp_server_only_starts_when_its_profile_is_asked_for() -> None:
    # The MCP server speaks stdio. With no stdin attached it reads EOF, exits 0 and, under
    # `restart: unless-stopped`, loops. The agents build their tools in-process, so nothing
    # needs the container to be running.
    mcp = _services()["mcp"]

    assert mcp.get("profiles") == [MCP_PROFILE]
