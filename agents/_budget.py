"""Per-agent budgets: context size, run tokens, turns and per-tool call caps.

Tool results are cut to fit before they reach the model, so a large diff cannot push the
agent's context past its budget. Tokens are estimated from characters because the provider
only reports real usage after a call.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

CHARS_PER_TOKEN = 4
NOTE_RESERVE_CHARS = 64
TRUNCATION_NOTE = "[truncated: {lines} lines, ask for a narrower path]"


@dataclass(frozen=True)
class AgentBudget:
    """How much one agent run may read, spend and call.

    Attributes:
        context_tokens: Most estimated tokens the conversation may hold.
        run_tokens: Most tokens (input plus output, as reported) the run may spend.
        max_turns: Most model calls, not counting the one reserved repair turn.
        call_caps: Most calls per tool name in one run; tools not listed are uncapped.
        max_result_bytes: Most bytes of one result per tool name, before the token cut.
    """

    context_tokens: int
    run_tokens: int
    max_turns: int
    call_caps: Mapping[str, int] = field(default_factory=dict[str, int])
    max_result_bytes: Mapping[str, int] = field(default_factory=dict[str, int])

    def cap_reached(self, name: str, counts: "CallCounts") -> bool:
        """Say whether the run has already used every call it may make to ``name``."""
        cap = self.call_caps.get(name)
        return cap is not None and counts.of(name) >= cap


@dataclass(frozen=True)
class CallCounts:
    """How many times each tool has run so far in one agent run."""

    counts: Mapping[str, int] = field(default_factory=dict[str, int])

    def of(self, name: str) -> int:
        """Return how often ``name`` has run."""
        return self.counts.get(name, 0)

    def plus(self, name: str) -> "CallCounts":
        """Return new counts with one more call to ``name``."""
        return CallCounts({**self.counts, name: self.of(name) + 1})


def estimate_tokens(text: str) -> int:
    """Estimate the tokens in ``text``, rounding up so the budget errs on the safe side."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def fit_to_budget(text: str, max_tokens: int, max_bytes: int | None = None) -> str:
    """Cut a tool result to fit the budget, on a line boundary, and say how much was cut.

    Args:
        text: The tool result as the model would see it.
        max_tokens: Estimated tokens the result may take.
        max_bytes: Optional byte cap for this tool, applied as well as the token cap.

    Returns:
        ``text`` unchanged when it fits; otherwise the whole lines that fit, followed by
        ``[truncated: N lines, ask for a narrower path]``. A first line too long to fit on
        its own is cut mid-line.
    """
    if _fits(text, max(max_tokens, 0) * CHARS_PER_TOKEN, max_bytes):
        return text
    char_limit = max(max_tokens * CHARS_PER_TOKEN - NOTE_RESERVE_CHARS, 0)
    byte_limit = None if max_bytes is None else max(max_bytes - NOTE_RESERVE_CHARS, 0)
    lines = text.splitlines(keepends=True)
    kept = _whole_lines_that_fit(lines, char_limit, byte_limit)
    if kept:
        body = "".join(lines[:kept])
        dropped = len(lines) - kept
    else:
        body = _cut_line(lines[0], char_limit, byte_limit) if lines else ""
        dropped = len(lines)
    note = TRUNCATION_NOTE.format(lines=dropped)
    body = body.rstrip("\n")
    return f"{body}\n{note}" if body else note


def _fits(text: str, char_limit: int, byte_limit: int | None) -> bool:
    """Say whether ``text`` is within both the character and the byte limit."""
    if len(text) > char_limit:
        return False
    return byte_limit is None or len(text.encode()) <= byte_limit


def _whole_lines_that_fit(lines: list[str], char_limit: int, byte_limit: int | None) -> int:
    """Count how many leading lines fit inside both limits together."""
    chars = 0
    size = 0
    for index, line in enumerate(lines):
        chars += len(line)
        size += len(line.encode())
        if chars > char_limit or (byte_limit is not None and size > byte_limit):
            return index
    return len(lines)


def _cut_line(line: str, char_limit: int, byte_limit: int | None) -> str:
    """Cut one over-long line to the limits, never splitting a UTF-8 character."""
    cut = line[:char_limit]
    if byte_limit is None:
        return cut
    return cut.encode()[:byte_limit].decode(errors="ignore")
