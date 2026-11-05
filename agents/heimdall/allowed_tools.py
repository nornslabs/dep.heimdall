"""MCP tools Heimdall may call for its review desk.

``load_skill`` and ``submit_result`` are local tools provided by the runtime; they are not
listed here and must not be added. ``get_diff`` and ``get_file`` are intentionally absent:
Heimdall reads only stored results, never raw diffs or source files.
"""

ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "get_pr_context",
        "get_repro",
        "list_findings",
        "save_card",
        "save_draft",
        "draft_close",
        "draft_reopen",
        "draft_merge",
        "draft_ask_contributor",
        "create_mechanical_task",
        "recall",
    }
)

ASK_TOOLS: frozenset[str] = frozenset({"get_pr_context", "get_repro", "list_findings", "recall"})
"""Read-only tools a mention may use in ask mode."""

DRAFT_TOOLS: frozenset[str] = frozenset(
    {"save_draft", "draft_close", "draft_reopen", "draft_merge", "draft_ask_contributor"}
)
"""Every tool that saves a draft for approval. They share one per-command draft cap."""

ACT_TOOLS: frozenset[str] = ASK_TOOLS | DRAFT_TOOLS
"""Ask tools plus the draft tools; a mention never opens ``save_card`` or a mechanical task."""
