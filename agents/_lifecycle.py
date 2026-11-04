"""Agent process startup checks: warn when Loki shares a model with another agent."""

from collections.abc import Mapping

from heimdall.adapters.ai import AiConfig
from heimdall.shared.logging import get_logger

log = get_logger(__name__)


def warn_if_shared_model(loki: AiConfig, others: Mapping[str, AiConfig]) -> bool:
    """Warn when Loki uses the same model as another agent, since its review is less independent.

    Args:
        loki: Loki's model settings.
        others: Other agents' settings, by agent name.

    Returns:
        True when at least one other agent shares Loki's model string.
    """
    shared = sorted(name for name, config in others.items() if config.model == loki.model)
    if not shared:
        return False
    log.warning("loki_same_model_as_other_agents", agents=shared)
    return True
