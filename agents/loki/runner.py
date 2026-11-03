"""Provider-neutral Loki model runner."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from agents._runtime import ToolBox, run_tool_loop
from agents.loki.config import create_ai_adapter
from heimdall.adapters.ai import AiAdapter, ChatMessage, ChatResponse


class AiReviewRunner:
    """Run Loki's read-only review prompt through the shared AI adapter."""

    def __init__(
        self,
        ai: AiAdapter | None = None,
        record_usage: Callable[[ChatResponse], Awaitable[None]] | None = None,
        on_notice: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """Bind Loki's configured adapter and optional runtime callbacks."""
        self._ai = ai or create_ai_adapter()
        self._record_usage = record_usage
        self._on_notice = on_notice

    async def review(self, prompt: str) -> str:
        """Return the model's text response for a trust-marked review prompt.

        Args:
            prompt: System instructions plus repository content marked as data.

        Returns:
            The model's text response.
        """
        result = await run_tool_loop(
            self._ai,
            ToolBox((), {}, frozenset()),
            (ChatMessage("user", prompt),),
            record_usage=self._record_usage,
            on_notice=self._on_notice,
        )
        return result.text

    async def aclose(self) -> None:
        """Close the HTTP client owned by this runner's adapter."""
        await self._ai.aclose()

    async def __aenter__(self) -> AiReviewRunner:
        """Allow the review flow to scope the provider client to one review."""
        return self

    async def __aexit__(self, *_exc: object) -> None:
        """Close provider resources after a review."""
        await self.aclose()
