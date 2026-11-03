"""Heimdall's provider configuration."""

from agents._config import load_ai_config as _load_ai_config
from heimdall.adapters.ai import AiAdapter, AiConfig


def load_ai_config(environ: dict[str, str] | None = None) -> AiConfig:
    """Load Heimdall's settings with shared default fallback.

    Args:
        environ: Optional environment mapping for deterministic callers.

    Returns:
        Heimdall's resolved provider settings.
    """
    return _load_ai_config("heimdall", environ)


def create_ai_adapter() -> AiAdapter:
    """Create Heimdall's provider adapter from its environment configuration.

    Returns:
        An adapter configured for Heimdall's provider.
    """
    return AiAdapter(load_ai_config())
