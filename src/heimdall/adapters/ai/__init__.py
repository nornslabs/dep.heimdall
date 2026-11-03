"""Provider-agnostic model access for agents. Core modules never import this package."""

from heimdall.adapters.ai.adapter import AiAdapter
from heimdall.adapters.ai.config import AiConfig
from heimdall.adapters.ai.types import (
    ChatMessage,
    ChatResponse,
    Role,
    ToolCall,
    ToolChoice,
    ToolSpec,
    Usage,
)

__all__ = [
    "AiAdapter",
    "AiConfig",
    "ChatMessage",
    "ChatResponse",
    "Role",
    "ToolCall",
    "ToolChoice",
    "ToolSpec",
    "Usage",
]
