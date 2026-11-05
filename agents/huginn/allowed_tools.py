"""MCP tools that Huginn may call."""

ALLOWED_TOOLS: frozenset[str] = frozenset(
    {"get_pr_context", "get_diff", "get_file", "start_repro", "get_repro", "recall"}
)

ASK_TOOLS: frozenset[str] = ALLOWED_TOOLS - {"start_repro"}
"""Read-only tools a mention may use in ask mode."""

ACT_TOOLS: frozenset[str] = ASK_TOOLS | {"start_repro"}
"""Ask tools plus the one write a maintainer's request to act opens."""
