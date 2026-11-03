"""Registry and lifecycle for provider-neutral AI requests."""

from __future__ import annotations

from collections.abc import Sequence

import httpx

from heimdall.adapters.ai.config import AiConfig
from heimdall.adapters.ai.errors import AiBadResponse
from heimdall.adapters.ai.providers import AnthropicProvider, OpenAiCompatibleProvider, Provider
from heimdall.adapters.ai.types import ChatMessage, ChatResponse, ToolDefinition

_PROVIDERS: dict[str, Provider] = {
    "openai_compat": OpenAiCompatibleProvider(),
    "anthropic": AnthropicProvider(),
}


def register_provider(name: str, provider: Provider) -> None:
    """Register a provider implementation so agents can select it by name.

    Args:
        name: Stable, case-insensitive provider key.
        provider: Object implementing the provider chat contract.

    Raises:
        ValueError: The provider name is blank.
    """
    normalized = name.strip().lower()
    if not normalized:
        raise ValueError("provider name is required")
    _PROVIDERS[normalized] = provider


class AiAdapter:
    """Send provider-neutral chat requests over plain HTTP."""

    def __init__(self, config: AiConfig, client: httpx.AsyncClient | None = None) -> None:
        """Bind provider settings and an optional externally managed HTTP client.

        Args:
            config: Provider, endpoint, and model settings.
            client: Optional caller-owned HTTP client, useful for shared pools.
        """
        self._config = config
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition] = (),
        tool_choice: str = "auto",
    ) -> ChatResponse:
        """Send one chat turn and return normalized text, tools, and usage.

        Args:
            messages: Conversation history in provider-neutral form.
            tools: Functions the model may request from the agent runtime.
            tool_choice: Whether the model may, must, or cannot call a tool.

        Returns:
            Normalized response content, tool calls, token usage, and model.

        Raises:
            ValueError: ``tool_choice`` is outside the supported choices.
            AiBadResponse: Provider is unknown or the response is malformed.
            AiUnavailable: Network, rate-limit, or provider-server failure.
        """
        provider = _PROVIDERS.get(self._config.provider.lower())
        if provider is None:
            raise AiBadResponse(f"unknown AI provider: {self._config.provider}")
        if tool_choice not in {"auto", "none", "required"}:
            raise ValueError("tool_choice must be auto, none, or required")
        return await provider.chat(self._client, self._config, messages, tools, tool_choice)

    async def aclose(self) -> None:
        """Close the HTTP client when this adapter created it."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> AiAdapter:
        """Allow an adapter to own its HTTP lifecycle in an async context."""
        return self

    async def __aexit__(self, *_exc: object) -> None:
        """Close resources owned by this adapter."""
        await self.aclose()
