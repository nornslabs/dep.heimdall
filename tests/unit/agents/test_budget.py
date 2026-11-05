"""Context budget: tool results are cut on a line and carry a truncation note."""

from agents._budget import (
    CHARS_PER_TOKEN,
    AgentBudget,
    CallCounts,
    estimate_tokens,
    fit_to_budget,
)


def _lines(count: int, width: int = 39) -> str:
    return "".join(f"{'x' * width}\n" for _ in range(count))


def test_result_within_budget_is_unchanged() -> None:
    text = _lines(3)

    assert fit_to_budget(text, max_tokens=1_000) == text


def test_overflowing_result_is_cut_on_a_line_with_the_note() -> None:
    text = _lines(100)

    fitted = fit_to_budget(text, max_tokens=200)

    body, note = fitted.rsplit("\n", 1)
    kept = body.split("\n")
    assert all(line == "x" * 39 for line in kept)
    assert note == f"[truncated: {100 - len(kept)} lines, ask for a narrower path]"
    assert len(fitted) <= 200 * CHARS_PER_TOKEN


def test_single_long_line_is_cut_mid_line() -> None:
    text = "y" * 10_000

    fitted = fit_to_budget(text, max_tokens=100)

    body, note = fitted.rsplit("\n", 1)
    assert body and set(body) == {"y"}
    assert note == "[truncated: 1 lines, ask for a narrower path]"


def test_byte_cap_cuts_a_result_that_fits_the_token_budget() -> None:
    text = _lines(100)

    fitted = fit_to_budget(text, max_tokens=1_000_000, max_bytes=400)

    body = fitted.rsplit("\n", 1)[0]
    assert len(body.encode()) <= 400
    assert fitted.endswith("ask for a narrower path]")


def test_no_room_left_returns_only_the_note() -> None:
    assert fit_to_budget(_lines(5), max_tokens=0) == (
        "[truncated: 5 lines, ask for a narrower path]"
    )


def test_estimate_rounds_up_to_whole_tokens() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcde") == 2


def test_call_cap_is_reached_after_the_allowed_number_of_calls() -> None:
    budget = AgentBudget(context_tokens=1, run_tokens=1, max_turns=1, call_caps={"get_file": 2})
    counts = CallCounts().plus("get_file").plus("get_file")

    assert budget.cap_reached("get_file", counts) is True
    assert budget.cap_reached("get_file", CallCounts().plus("get_file")) is False
    assert budget.cap_reached("get_diff", counts) is False


def test_call_counts_never_change_in_place() -> None:
    first = CallCounts()

    second = first.plus("get_file")

    assert (first.of("get_file"), second.of("get_file")) == (0, 1)
