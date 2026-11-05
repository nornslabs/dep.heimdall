"""Tests for Heimdall's brief and prompt construction."""

import json
import re

from agents.heimdall.context import build_desk_brief, build_messages

_DATA_TAG = re.compile(r'<pr_data source="([^"]+)">\n(.*?)\n</pr_data>', re.DOTALL)


def test_build_desk_brief_populates_all_fields() -> None:
    brief = build_desk_brief(
        installation_id=42,
        pr_id="00000000-0000-0000-0000-000000000001",
        repo_full_name="owner/repo",
        number=7,
        title="Fix typo",
        head_sha="head123",
        repro_summary="Fix confirmed.",
        finding_summaries=("[f-1] src/a.py:1 — Typo",),
        command_text="@heimdall thank",
    )

    assert brief.installation_id == 42
    assert brief.pr_id == "00000000-0000-0000-0000-000000000001"
    assert brief.repo_full_name == "owner/repo"
    assert brief.number == 7
    assert brief.title == "Fix typo"
    assert brief.head_sha == "head123"
    assert brief.repro_summary == "Fix confirmed."
    assert brief.finding_summaries == ("[f-1] src/a.py:1 — Typo",)
    assert brief.command_text == "@heimdall thank"


def test_build_messages_for_card_contains_brief_in_pr_data() -> None:
    brief = build_desk_brief(
        installation_id=42,
        pr_id="00000000-0000-0000-0000-000000000001",
        repo_full_name="owner/repo",
        number=7,
        title="Unsafe <script> title",
        head_sha="head123",
        repro_summary="Fix confirmed.",
        finding_summaries=("src/a.py:1 — Typo",),
    )
    system, user = build_messages(brief, "You are Heimdall.", "## Skills\n- write_card: ...")

    assert system.role == "system"
    assert "You are Heimdall." in system.content
    assert "## Skills" in system.content

    assert user.role == "user"
    assert "installation_id 42 and pr_id 00000000-0000-0000-0000-000000000001" in user.content

    matches = _DATA_TAG.findall(user.content)
    assert len(matches) == 1
    source, payload_str = matches[0]
    assert source == "pr_brief"
    payload = json.loads(payload_str)
    assert payload["repository"] == "owner/repo"
    assert payload["pull_request"]["number"] == 7
    assert payload["pull_request"]["head_sha"] == "head123"
    assert payload["repro_status"] == "Fix confirmed."
    assert payload["findings"] == ["src/a.py:1 — Typo"]


def test_build_messages_with_command_includes_maintainer_command_block() -> None:
    brief = build_desk_brief(
        installation_id=42,
        pr_id="00000000-0000-0000-0000-000000000001",
        repo_full_name="owner/repo",
        number=7,
        title="Title",
        head_sha="head123",
        command_text="/heimdall thank",
    )
    _system, user = build_messages(brief, "Role prompt", "")

    matches = _DATA_TAG.findall(user.content)
    assert len(matches) == 2
    sources = [source for source, _content in matches]
    assert sources == ["pr_brief", "maintainer_command"]
    assert matches[1][1] == "/heimdall thank"
