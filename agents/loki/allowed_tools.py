"""MCP tools Loki may call; this agent has no write or reproduction controls."""

ALLOWED_TOOLS: frozenset[str] = frozenset({"get_pr_context", "get_diff", "save_findings"})
