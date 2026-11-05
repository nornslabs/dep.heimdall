"""Loki receives only read-context and finding-submission MCP tools."""

import runpy
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
ALLOWED_TOOLS = runpy.run_path(str(REPOSITORY_ROOT / "agents/loki/allowed_tools.py"))[
    "ALLOWED_TOOLS"
]


def test_loki_tool_scope_excludes_all_other_tools() -> None:
    """Keep Loki's exposed tool list exact and read-only except saving findings."""
    assert {"get_pr_context", "get_diff", "save_findings", "recall"} == ALLOWED_TOOLS
    assert not ALLOWED_TOOLS & {"remember", "forget"}
    assert "start_repro" not in ALLOWED_TOOLS
    assert "get_repro" not in ALLOWED_TOOLS
