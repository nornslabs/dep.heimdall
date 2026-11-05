"""MCP tools Loki may call; it has no reproduction controls and no memory write tool."""

ALLOWED_TOOLS: frozenset[str] = frozenset({"get_pr_context", "get_diff", "save_findings", "recall"})
