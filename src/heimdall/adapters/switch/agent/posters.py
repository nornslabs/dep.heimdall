"""Build one poster per agent whose Switch id and key are configured."""

import os
from collections.abc import Mapping

from heimdall.adapters.switch.agent._post import AgentPoster
from heimdall.adapters.switch.agent.credentials import credential_names, load_agent_credentials
from heimdall.shared.agents import AgentName


def build_agent_posters(
    base_url: str, environ: Mapping[str, str] | None = None
) -> dict[AgentName, AgentPoster]:
    """Create a poster for each agent that has a Switch id or key set.

    An agent with neither variable is left out, so a deployment can run with only some agents
    registered; rows naming it then fail rather than post under another name. An agent with
    only one of the two variables is a mistake and fails at startup.

    Args:
        base_url: Switch API root.
        environ: Variables to read; the process environment when omitted.

    Returns:
        Poster for each configured agent.

    Raises:
        ConfigInvalid: an agent has one of its two variables but not the other. The error names
            the missing variable, never a value.
    """
    env = os.environ if environ is None else environ
    posters: dict[AgentName, AgentPoster] = {}
    for name in AgentName:
        if not _has_any_credential(env, name):
            continue
        config = load_agent_credentials(name.value, base_url, rooms=(), environ=env)
        posters[name] = AgentPoster(config)
    return posters


def _has_any_credential(env: Mapping[str, str], name: AgentName) -> bool:
    """Whether the agent's id or key variable holds a non-blank value."""
    return any(env.get(variable, "").strip() for variable in credential_names(name.value))
