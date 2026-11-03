"""Provider-neutral HTTP adapter for agent model requests."""

from heimdall.adapters.ai.adapter import AiAdapter, register_provider
from heimdall.adapters.ai.config import AiConfig
from heimdall.adapters.ai.errors import AiBadResponse, AiUnavailable
from heimdall.adapters.ai.types import (
    ChatMessage,
    ChatResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)

__all__ = [
    "AiAdapter",
    "AiBadResponse",
    "AiConfig",
    "AiUnavailable",
    "ChatMessage",
    "ChatResponse",
    "TokenUsage",
    "ToolCall",
    "ToolDefinition",
    "register_provider",
]
