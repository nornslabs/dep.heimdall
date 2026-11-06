"""Which tools Heimdall's mention runs may use, and what each one counts as."""

from agents._evidence import ACTION_FOR_TOOL
from agents.heimdall.allowed_tools import ACT_TOOLS, ALLOWED_TOOLS, ASK_TOOLS

ACTION_TOOLS = frozenset({"draft_close", "draft_reopen", "draft_merge", "draft_ask_contributor"})


def test_action_tools_open_only_in_act_mode() -> None:
    assert ACTION_TOOLS <= ACT_TOOLS
    assert not ACTION_TOOLS & ASK_TOOLS


def test_action_tools_are_on_the_server_allowlist() -> None:
    assert ACTION_TOOLS <= ALLOWED_TOOLS


def test_each_action_tool_reports_as_a_saved_draft() -> None:
    for name in ACTION_TOOLS:
        assert ACTION_FOR_TOOL[name] == "draft_saved"


def test_recheck_opens_only_in_act_mode() -> None:
    assert "recheck_intake" in ACT_TOOLS
    assert "recheck_intake" not in ASK_TOOLS
    assert "recheck_intake" in ALLOWED_TOOLS


def test_recheck_reports_as_an_intake_rerun() -> None:
    assert ACTION_FOR_TOOL["recheck_intake"] == "intake_rerun"
