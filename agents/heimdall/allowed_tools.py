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
        "create_mechanical_task",
        "recall",
    }
)
