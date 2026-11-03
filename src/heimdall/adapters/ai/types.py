"""Typed request and response values shared by AI providers."""

from dataclasses import dataclass
from typing import Literal

MessageRole = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class ToolDefinition:
    """A JSON-schema function the model may ask the runtime to call."""

    name: str
    description: str
    parameters: dict[str, object]


@dataclass(frozen=True)
class ToolCall:
    """One validated function request returned by a provider."""

    id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class TokenUsage:
    """Input and output token counts reported by a provider."""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class ChatMessage:
    """One provider-neutral conversation message."""

    role: MessageRole
    content: str = ""
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class ChatResponse:
    """Text, tool requests, and usage returned by one model turn."""

    text: str
    tool_calls: tuple[ToolCall, ...]
    usage: TokenUsage
    model: str

    @property
    def input_tokens(self) -> int:
        """Return provider-reported input tokens for usage persistence."""
        return self.usage.input_tokens

    @property
    def output_tokens(self) -> int:
        """Return provider-reported output tokens for usage persistence."""
        return self.usage.output_tokens
