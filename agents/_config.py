"""Load one agent's AI settings with shared defaults and per-agent overrides."""

import os
from collections.abc import Mapping

from heimdall.adapters.ai import AiConfig

_DEFAULT_BASE_URLS = {
    "openai_compat": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
}


def load_ai_config(agent_name: str, environ: Mapping[str, str] | None = None) -> AiConfig:
    """Load provider settings for an agent, falling back field by field to defaults.

    Args:
        agent_name: One of ``huginn``, ``loki``, or ``heimdall``.
        environ: Optional environment mapping for deterministic callers.

    Returns:
        Resolved endpoint and model settings for the selected agent.

    Raises:
        ValueError: The agent name is unknown or no model is configured.
    """
    if agent_name not in {"huginn", "loki", "heimdall"}:
        raise ValueError(f"unknown agent: {agent_name}")
    values = os.environ if environ is None else environ
    prefix = f"LLM_{agent_name.upper()}_"
    provider = _setting(values, prefix + "PROVIDER", "LLM_DEFAULT_PROVIDER") or "openai_compat"
    base_url = _setting(values, prefix + "BASE_URL", "LLM_DEFAULT_BASE_URL")
    if not base_url:
        base_url = _DEFAULT_BASE_URLS.get(provider.lower())
    model = _setting(values, prefix + "MODEL", "LLM_DEFAULT_MODEL")
    if not model:
        raise ValueError("set LLM_DEFAULT_MODEL or the agent-specific LLM_<AGENT>_MODEL")
    if not base_url:
        raise ValueError("set LLM_DEFAULT_BASE_URL or the agent-specific LLM_<AGENT>_BASE_URL")
    api_key = _setting(values, prefix + "API_KEY", "LLM_DEFAULT_API_KEY")
    return AiConfig(provider, base_url, api_key or None, model)


def _setting(values: Mapping[str, str], specific: str, default: str) -> str | None:
    """Use a nonblank agent value, otherwise inherit its shared default.

    Args:
        values: Environment values available to the loader.
        specific: Agent-specific variable name.
        default: Shared fallback variable name.

    Returns:
        Nonblank agent value, shared value, or ``None``.
    """
    value = values.get(specific)
    if value is not None and value.strip():
        return value
    return values.get(default) or None
