"""Provider-neutral chat values that cross between agents and the AI adapter."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

Role = Literal["system", "user", "assistant", "tool"]
ToolChoice = Literal["auto", "none", "required"]


@dataclass(frozen=True)
class ToolSpec:
    """One tool the model may call, described with a JSON Schema for its arguments."""

    name: str
    description: str
    input_schema: Mapping[str, object]


@dataclass(frozen=True)
class ToolCall:
    """A model's request to run one tool with parsed JSON arguments."""

    id: str
    name: str
    arguments: Mapping[str, object]


@dataclass(frozen=True)
class ChatMessage:
    """One turn of conversation history, in the adapter's own neutral shape."""

    role: Role
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True)
class Usage:
    """Tokens one or more model calls consumed."""

    input_tokens: int
    output_tokens: int

    def __add__(self, other: "Usage") -> "Usage":
        """Return the combined token counts of two usages without changing either.

        Args:
            other: Usage to add to this one.

        Returns:
            A new Usage holding both sums.
        """
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
        )


@dataclass(frozen=True)
class ChatResponse:
    """A model reply: text, tool calls, or both, plus what it cost and who answered."""

    text: str | None
    tool_calls: tuple[ToolCall, ...]
    usage: Usage
    model: str
