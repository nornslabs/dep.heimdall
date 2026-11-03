"""MCP tools Heimdall may call for its review desk."""

ALLOWED_TOOLS: frozenset[str] = frozenset({"list_findings", "get_repro", "save_card", "save_draft"})
