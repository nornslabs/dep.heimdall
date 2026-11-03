"""Heimdall card assembly and maintainer command routing."""

import runpy
import uuid
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
HEIMDALL = runpy.run_path(str(REPOSITORY_ROOT / "agents/heimdall/agent.py"))
build_review_card = HEIMDALL["build_review_card"]
handle_command = HEIMDALL["handle_command"]


class FakeTools:
    """Collect card and draft calls and return current PR data."""

    def __init__(self) -> None:
        self.card: dict[str, object] | None = None
        self.draft: dict[str, object] | None = None

    async def list_findings(self, installation_id: int, pr_id: str) -> list[dict[str, object]]:
        return [{"file": "src/a.py", "line": 3, "text": "Missing check."}]

    async def get_repro(self, installation_id: int, run_id: str) -> dict[str, object]:
        return {"status": "finished", "verdict": "fix_confirmed"}

    async def save_card(
        self,
        installation_id: int,
        pr_id: str,
        number: int,
        title: str,
        head_sha: str,
        rules_summary: str,
        fix_status: str,
        gaps: list[str],
        suggested_action: str,
    ) -> dict[str, str]:
        self.card = {"title": title, "gaps": gaps, "fix_status": fix_status}
        return {"card_id": str(uuid.uuid4())}

    async def save_draft(
        self,
        installation_id: int,
        pr_id: str,
        head_sha: str,
        kind: str,
        body: str,
    ) -> dict[str, str]:
        self.draft = {"kind": kind, "body": body}
        return {"draft_id": str(uuid.uuid4()), "body_hash": "a" * 64}


class FakeHandlers:
    """Record dispatches for each recognized command."""

    def __init__(self) -> None:
        self.actions: list[str] = []

    async def approve(self, command: object) -> str:
        self.actions.append("approve")
        return "approval recorded"

    async def revise(self, command: object) -> str:
        self.actions.append("revise")
        return "replacement draft saved"

    async def fix(self, command: object) -> str:
        self.actions.append("fix")
        return "fix task queued"

    async def retry(self, command: object) -> str:
        self.actions.append("retry")
        return "reproduction queued"

    async def pause_auto(self, command: object) -> str:
        self.actions.append("pause_auto")
        return "automatic suggestions paused"


async def test_build_review_card_saves_safe_matching_card_and_draft() -> None:
    """Store a current-head card and an approval-bound rendering of the same content."""
    tools = FakeTools()
    result = await build_review_card(
        tools,
        456,
        uuid.uuid4(),
        7,
        "owner/repo",
        "*unsafe title*",
        "head-7",
        "All checks pass",
        "Ask maintainer to review",
        uuid.uuid4(),
    )

    assert result.body_hash == "a" * 64
    assert tools.card is not None
    assert (
        tools.card["fix_status"]
        == "Fix confirmed: tests passed with the fix and failed without it."
    )
    assert tools.draft is not None
    assert r"\*unsafe title\*" in str(tools.draft["body"])
    assert r"src/a\.py:3" in str(tools.draft["body"])
    assert "draft_id=" + str(result.draft_id) in result.approval_message
    assert "repo=owner/repo" in result.approval_message


async def test_handle_command_dispatches_supported_commands_and_ignores_other_text() -> None:
    """Only valid slash commands reach their corresponding trusted handlers."""
    handlers = FakeHandlers()
    draft_id = uuid.uuid4()
    finding_id = uuid.uuid4()

    assert await handle_command("hello", handlers) is None
    await handle_command(f"/heimdall approve {draft_id}", handlers)
    await handle_command(f"/heimdall revise {draft_id} clearer text", handlers)
    await handle_command(f"/heimdall fix {finding_id}", handlers)
    await handle_command("/heimdall retry", handlers)

    await handle_command("@heimdall pause auto", handlers)
    assert handlers.actions == ["approve", "revise", "fix", "retry", "pause_auto"]
