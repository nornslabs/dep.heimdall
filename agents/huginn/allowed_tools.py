"""MCP tools that Huginn may call."""

ALLOWED_TOOLS: frozenset[str] = frozenset(
    {"get_pr_context", "get_diff", "get_file", "start_repro", "get_repro", "recall"}
)
