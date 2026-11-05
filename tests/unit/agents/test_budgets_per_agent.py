"""Each agent's starting budget matches the HEI-84 table."""

from agents._budget import CallCounts
from agents.heimdall.config import BUDGET as HEIMDALL
from agents.huginn.config import BUDGET as HUGINN
from agents.loki.config import BUDGET as LOKI


def test_context_budgets_match_the_table() -> None:
    assert (HUGINN.context_tokens, LOKI.context_tokens, HEIMDALL.context_tokens) == (
        24_000,
        40_000,
        24_000,
    )


def test_diff_size_caps_match_the_table() -> None:
    assert HUGINN.max_result_bytes["get_diff"] == 40 * 1024
    assert LOKI.max_result_bytes["get_diff"] == 80 * 1024


def test_file_read_caps_match_the_table() -> None:
    assert HUGINN.call_caps["get_file"] == 4
    assert LOKI.call_caps["get_file"] == 8


def test_huginn_may_start_at_most_three_repro_runs() -> None:
    assert HUGINN.call_caps["start_repro"] == 3


def test_heimdall_reads_no_diff_or_file_and_drafts_at_most_twice() -> None:
    nothing_yet = CallCounts()
    two_drafts = nothing_yet.plus("save_draft").plus("save_draft")

    assert HEIMDALL.cap_reached("get_diff", nothing_yet) is True
    assert HEIMDALL.cap_reached("get_file", nothing_yet) is True
    assert HEIMDALL.cap_reached("save_draft", two_drafts) is True
