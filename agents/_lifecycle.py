"""Agent process lifecycle: stay alive until stopped, and warn when Loki shares a model."""

import asyncio
import signal
from collections.abc import Mapping

from heimdall.adapters.ai import AiConfig
from heimdall.shared.logging import get_logger

log = get_logger(__name__)


async def wait_until_stopped(agent: str, model: str) -> None:
    """Keep a configured agent process alive until SIGTERM or SIGINT.

    Room-event wake-up is not wired yet, so the process only proves its model config
    loads and then waits; ``docker compose`` sees a healthy, running service.

    Args:
        agent: Agent name for the log line.
        model: Configured model name; logged, never the key.
    """
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for stop_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(stop_signal, stop.set)
    log.info("agent_ready", agent=agent, model=model)
    await stop.wait()
    log.info("agent_stopped", agent=agent)


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
