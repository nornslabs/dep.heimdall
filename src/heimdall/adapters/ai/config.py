"""Model settings for one agent, read from LLM_<AGENT>_* with an LLM_DEFAULT_* fallback."""

import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, SecretStr

from heimdall.shared.errors import ConfigInvalid

DEFAULT_PROVIDER = "openai_compat"
ENV_PREFIX = "LLM"
DEFAULT_SCOPE = "DEFAULT"
REQUIRED_FIELDS = ("base_url", "api_key", "model")


class AiConfig(BaseModel):
    """Where and how one agent reaches its model. The key never appears in a repr.

    Attributes:
        provider: Key in the provider registry, ``openai_compat`` by default.
        base_url: Provider root, for example ``https://openrouter.ai/api/v1``.
        api_key: Provider credential.
        model: Model name sent with every request and recorded with usage.
        temperature: Sampling temperature.
        max_output_tokens: Upper bound on tokens in one reply.
        timeout_s: HTTP timeout for one request, in seconds.
    """

    model_config = ConfigDict(frozen=True)

    provider: str = DEFAULT_PROVIDER
    base_url: str
    api_key: SecretStr
    model: str
    temperature: float = 0.2
    max_output_tokens: int = 4096
    timeout_s: float = 120

    @classmethod
    def from_env(cls, agent: str, environ: Mapping[str, str] | None = None) -> "AiConfig":
        """Read LLM_<AGENT>_<FIELD>, falling back to LLM_DEFAULT_<FIELD>.

        Args:
            agent: Agent name such as ``loki``; upper-cased into the variable names.
            environ: Variables to read; the process environment when omitted.

        Returns:
            The agent's validated model settings.

        Raises:
            ConfigInvalid: a required value is unset under both names, or the provider
                is not registered.
        """
        env = os.environ if environ is None else environ
        values: dict[str, str] = {}
        for field in (*REQUIRED_FIELDS, "provider"):
            value = _lookup(env, agent, field)
            if value is not None:
                values[field] = value
            elif field in REQUIRED_FIELDS:
                raise ConfigInvalid(f"{_names(agent, field)} is not set")
        config = cls.model_validate(values)
        _require_known_provider(config.provider, _names(agent, "provider"))
        return config


def _lookup(env: Mapping[str, str], agent: str, field: str) -> str | None:
    """Return the agent's value, else the default, treating blank values as unset."""
    for scope in (agent.upper(), DEFAULT_SCOPE):
        value = env.get(f"{ENV_PREFIX}_{scope}_{field.upper()}", "").strip()
        if value:
            return value
    return None


def _names(agent: str, field: str) -> str:
    """Name both variables tried for one field, for error messages."""
    suffix = field.upper()
    return f"{ENV_PREFIX}_{agent.upper()}_{suffix} or {ENV_PREFIX}_{DEFAULT_SCOPE}_{suffix}"


def _require_known_provider(provider: str, variables: str) -> None:
    """Fail at startup instead of at the first chat when the provider is unknown."""
    from heimdall.adapters.ai.providers import PROVIDERS

    if provider not in PROVIDERS:
        known = ", ".join(sorted(PROVIDERS))
        raise ConfigInvalid(f"{variables} names unknown provider {provider!r}; known: {known}")
