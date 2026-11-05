"""Which draft tools take effect from the maintainer's own message, and which wait."""

import uuid
from typing import cast

import pytest

import agents.heimdall._auto_approve as auto_approve
from agents._mention_ports import ActSetup
from agents._toolbox import AgentTool
from heimdall.adapters.ai import ToolSpec
from heimdall.shared.errors import DraftChanged, NotAMaintainer
from heimdall.shared.ids import InstallationId

DRAFT_ID = str(uuid.uuid4())
BODY_HASH = "c" * 64


class Recorder:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.error = error

    async def __call__(self, *args: object, **kwargs: object) -> None:
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error


def _setup(sender: str = "alice") -> ActSetup:
    pr = type("Pr", (), {"repo_full_name": "octo/demo"})()
    ctx = type("Ctx", (), {"installation_id": InstallationId(5)})()
    return cast(
        ActSetup,
        type(
            "Setup",
            (),
            {"sessions": object(), "reader": object(), "ctx": ctx, "pr": pr, "sender": sender},
        )(),
    )


def _tool(name: str) -> AgentTool:
    async def handler(arguments: object) -> object:
        return {"draft_id": DRAFT_ID, "body_hash": BODY_HASH}

    return AgentTool(ToolSpec(name, "d", {"type": "object"}), handler)


def _status(result: object) -> str:
    return str(cast(dict[str, object], result)["status"])


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["draft_close", "draft_reopen", "draft_ask_contributor"])
async def test_these_drafts_are_approved_by_the_maintainers_message(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    record = Recorder()
    monkeypatch.setattr(auto_approve, "record_approval", record)

    result = await auto_approve.approve_on_save(_setup(), _tool(name)).handler({"body": "x"})

    assert len(record.calls) == 1
    args, kwargs = record.calls[0]
    assert uuid.UUID(DRAFT_ID) in args
    assert "alice" in args
    assert BODY_HASH in args
    assert kwargs == {"via": "message"}
    assert "queued" in _status(result)


@pytest.mark.asyncio
async def test_a_merge_draft_waits_for_its_own_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    record = Recorder()
    monkeypatch.setattr(auto_approve, "record_approval", record)

    result = await auto_approve.approve_on_save(_setup(), _tool("draft_merge")).handler({})

    assert record.calls == []
    assert "not posted" in _status(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [NotAMaintainer("no"), DraftChanged("moved")])
async def test_a_refused_approval_leaves_the_draft_unapproved(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    monkeypatch.setattr(auto_approve, "record_approval", Recorder(error))

    result = await auto_approve.approve_on_save(_setup(), _tool("draft_close")).handler({})

    assert "not approved" in _status(result)


@pytest.mark.asyncio
async def test_no_known_sender_means_nothing_is_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = Recorder()
    monkeypatch.setattr(auto_approve, "record_approval", record)

    result = await auto_approve.approve_on_save(_setup(sender=""), _tool("draft_close")).handler({})

    assert record.calls == []
    assert "not approved" in _status(result)


@pytest.mark.asyncio
async def test_a_refused_save_is_passed_through_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = Recorder()
    monkeypatch.setattr(auto_approve, "record_approval", record)

    async def refused(arguments: object) -> object:
        return "error: at most 2 drafts per command"

    tool = AgentTool(ToolSpec("draft_close", "d", {"type": "object"}), refused)

    result = await auto_approve.approve_on_save(_setup(), tool).handler({})

    assert result == "error: at most 2 drafts per command"
    assert record.calls == []
