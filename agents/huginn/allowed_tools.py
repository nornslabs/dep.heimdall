"""MCP tools that Huginn may call."""

ALLOWED_TOOLS: frozenset[str] = frozenset({"start_repro", "get_repro", "recall"})
