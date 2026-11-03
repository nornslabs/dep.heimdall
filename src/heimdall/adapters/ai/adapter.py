"""AiAdapter: the one way agents talk to a model, over plain HTTP."""

import asyncio
from collections.abc import Sequence

import httpx

from heimdall.adapters.ai._retry import Sleep, with_retry
from heimdall.adapters.ai.config import AiConfig
from heimdall.adapters.ai.providers import PROVIDERS, Provider
from heimdall.adapters.ai.types import ChatMessage, ChatResponse, ToolChoice, ToolSpec
from heimdall.shared.errors import ConfigInvalid


class AiAdapter:
    """The only way agents talk to a model. The provider is picked by ``config.provider``."""

    def __init__(
        self,
        config: AiConfig,
        *,
        http: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        """Bind one agent's model settings to an HTTP client.

        Args:
            config: Provider, base URL, key, model and limits.
            http: Client to reuse; the adapter creates and owns one when omitted.
            sleep: Delay used between retries; injectable for tests.
        """
        self._config = config
        self._owns_http = http is None
        self._http = http if http is not None else httpx.AsyncClient(timeout=config.timeout_s)
        self._sleep = sleep

    @property
    def model(self) -> str:
        """The configured model name, recorded with usage."""
        return self._config.model

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        *,
        tool_choice: ToolChoice = "auto",
    ) -> ChatResponse:
        """Send one chat turn and return the reply. Tool execution is the agent's job.

        Args:
            messages: Conversation history, oldest first.
            tools: Tools the model may call; empty for a plain completion.
            tool_choice: Whether the model may, must, or must not call a tool.

        Returns:
            Text and/or tool calls, with usage and the answering model.

        Raises:
            ConfigInvalid: the configured provider is not registered.
            AiUnavailable: the provider stayed unavailable through every retry.
            AiRequestFailed: the provider rejected the request.
            AiBadResponse: the reply could not be parsed.
        """
        provider = _provider(self._config.provider)
        history = tuple(messages)
        offered = tuple(tools)
        return await with_retry(
            lambda: provider.send(self._http, self._config, history, offered, tool_choice),
            self._sleep,
        )

    async def aclose(self) -> None:
        """Close the HTTP client if this adapter created it."""
        if self._owns_http:
            await self._http.aclose()


def _provider(name: str) -> Provider:
    """Look up a provider at call time so late registrations are honoured."""
    provider = PROVIDERS.get(name)
    if provider is None:
        raise ConfigInvalid(f"unknown model provider {name!r}")
    return provider
