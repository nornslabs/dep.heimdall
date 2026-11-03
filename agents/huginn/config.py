"""Huginn's provider configuration."""

from agents._config import load_ai_config as _load_ai_config
from heimdall.adapters.ai import AiAdapter, AiConfig


def load_ai_config(environ: dict[str, str] | None = None) -> AiConfig:
    """Load Huginn's settings with shared default fallback.

    Args:
        environ: Optional environment mapping for deterministic callers.

    Returns:
        Huginn's resolved provider settings.
    """
    return _load_ai_config("huginn", environ)


def create_ai_adapter() -> AiAdapter:
    """Create Huginn's provider adapter from its environment configuration.

    Returns:
        An adapter configured for Huginn's provider.
    """
    return AiAdapter(load_ai_config())
