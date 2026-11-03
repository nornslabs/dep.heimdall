"""Registry of model providers, keyed by the name used in ``AiConfig.provider``."""

from heimdall.adapters.ai.providers.anthropic import AnthropicNative
from heimdall.adapters.ai.providers.base import Provider
from heimdall.adapters.ai.providers.openai_compat import OpenAiCompat

PROVIDERS: dict[str, Provider] = {
    "openai_compat": OpenAiCompat(),
    "anthropic": AnthropicNative(),
}


def register_provider(name: str, provider: Provider) -> None:
    """Make a provider selectable by name through ``LLM_<AGENT>_PROVIDER``.

    Args:
        name: Registry key; replaces any provider already registered under it.
        provider: Object implementing the Provider protocol.

    Raises:
        ValueError: the name is blank.
    """
    if not name.strip():
        raise ValueError("provider name must not be blank")
    PROVIDERS[name] = provider


__all__ = ["PROVIDERS", "AnthropicNative", "OpenAiCompat", "Provider", "register_provider"]
