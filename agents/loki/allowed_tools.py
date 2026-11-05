"""MCP tools Loki may call; it has no reproduction controls and no memory write tool."""

ALLOWED_TOOLS: frozenset[str] = frozenset(
    {"get_pr_context", "get_diff", "save_findings", "recall", "get_file"}
)

ASK_TOOLS: frozenset[str] = ALLOWED_TOOLS - {"save_findings"}
"""Read-only tools a mention may use in ask mode."""

ACT_TOOLS: frozenset[str] = ASK_TOOLS | {"save_findings"}
"""Ask tools plus the one write a maintainer's request to act opens."""
