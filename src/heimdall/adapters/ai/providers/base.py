"""The Provider protocol every model backend implements."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

import httpx

from heimdall.adapters.ai.types import ChatMessage, ChatResponse, ToolChoice, ToolSpec

if TYPE_CHECKING:
    from heimdall.adapters.ai.config import AiConfig


class Provider(Protocol):
    """Turns neutral chat values into one HTTP request and its reply back into values."""

    async def send(
        self,
        http: httpx.AsyncClient,
        config: "AiConfig",
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
        tool_choice: ToolChoice,
    ) -> ChatResponse:
        """Send one chat request without retrying.

        Args:
            http: Shared HTTP client owned by the adapter.
            config: Base URL, key, model and limits for this agent.
            messages: Conversation history, oldest first.
            tools: Tools the model may call; empty for a plain completion.
            tool_choice: Whether the model may, must, or must not call a tool.

        Returns:
            The parsed reply.

        Raises:
            AiUnavailable: network error, timeout, 429 or 5xx.
            AiRequestFailed: any other 4xx.
            AiBadResponse: the reply cannot be parsed into a ChatResponse.
        """
        ...
