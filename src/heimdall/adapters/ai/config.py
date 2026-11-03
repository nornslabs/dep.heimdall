"""Immutable settings needed to call one configured AI provider."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AiConfig:
    """Provider endpoint and model settings for one agent.

    Attributes:
        provider: Provider registry key such as ``openai_compat`` or ``anthropic``.
        base_url: Provider API root, without a method-specific path.
        api_key: Optional API key, omitted from the value representation.
        model: Model identifier understood by the configured endpoint.
        max_tokens: Maximum generated tokens for a response.
        timeout_seconds: HTTP request deadline.
    """

    provider: str
    base_url: str
    api_key: str | None = field(repr=False)
    model: str
    max_tokens: int = 4096
    timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        """Reject incomplete endpoint configuration before making a request."""
        if not self.provider.strip() or not self.base_url.strip() or not self.model.strip():
            raise ValueError("AI provider, base URL, and model are required")
        if self.max_tokens <= 0 or self.timeout_seconds <= 0:
            raise ValueError("AI token limit and timeout must be positive")
