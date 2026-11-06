# adapters/ai

The only way agents talk to a model. It uses plain `httpx` and no vendor SDK or CLI. Only
`agents/` imports this package. Core modules in `src/heimdall/modules/` never do, and
`tests/unit/test_ai_boundaries.py` enforces both rules.

```python
from heimdall.adapters.ai import AiAdapter, AiConfig, ChatMessage, ToolSpec

ai = AiAdapter(AiConfig.from_env("loki"))
reply = await ai.chat([ChatMessage("system", rules), ChatMessage("user", data)], tools=(...,))
# reply: ChatResponse(text, tool_calls, usage, model)
await ai.aclose()
```

`chat` sends one turn and returns the reply. It never runs tools: the agent does that, either
with its own flow or with `agents/_runtime.run_tool_loop`.

| File | Role |
|---|---|
| `types.py` | Frozen `ChatMessage`, `ToolSpec`, `ToolCall`, `Usage`, `ChatResponse` |
| `config.py` | `AiConfig` and `AiConfig.from_env(agent)` |
| `adapter.py` | `AiAdapter`: `chat`, `model`, `aclose` |
| `_retry.py` | Retries `AiUnavailable` after 2s, 8s and 30s and honours `Retry-After` (capped at 60s) |
| `providers/` | `PROVIDERS` registry, `openai_compat` (default), native `anthropic` |

## Config

`AiConfig.from_env(agent)` reads `LLM_<AGENT>_<FIELD>` first and falls back to
`LLM_DEFAULT_<FIELD>`. Blank values count as unset.

| Field | Variable suffix | Default |
|---|---|---|
| `provider` | `_PROVIDER` | `openai_compat` |
| `base_url` | `_BASE_URL` | required |
| `api_key` | `_API_KEY` | required, `SecretStr`, never logged or shown in `repr` |
| `model` | `_MODEL` | required |
| `temperature` | | `0.2` |
| `max_output_tokens` | | `4096` |
| `timeout_s` | | `120` |

A missing value raises `ConfigInvalid` naming both variables it tried, for example
`LLM_LOKI_MODEL or LLM_DEFAULT_MODEL`. An unknown provider is also rejected at startup.

## Errors

| Error | When | Retried |
|---|---|---|
| `AiUnavailable` | network error, timeout, 429, 5xx | yes |
| `AiRequestFailed` | any other 4xx (bad key, bad model) | no |
| `AiBadResponse` | invalid JSON, unparseable tool arguments, neither text nor tool calls | no |

## Example base URLs

| Provider | `provider` | `base_url` |
|---|---|---|
| OpenRouter | `openai_compat` | `https://openrouter.ai/api/v1` |
| OpenAI | `openai_compat` | `https://api.openai.com/v1` |
| Anthropic native | `anthropic` | `https://api.anthropic.com` |
| Groq | `openai_compat` | `https://api.groq.com/openai/v1` |
| Ollama | `openai_compat` | `http://localhost:11434/v1` |

## Adding a provider

Implement the `Provider` protocol (`providers/base.py`) and register it before agents start:

```python
from heimdall.adapters.ai.providers import register_provider

register_provider("my_gateway", MyGateway())   # then LLM_<AGENT>_PROVIDER=my_gateway
```

`send` makes one request and does not retry. It raises `AiUnavailable`, `AiRequestFailed` or
`AiBadResponse` as in the table above. The helpers in `providers/_wire.py` already map HTTP
statuses to these errors.

## Related documentation

- `docs/agents.md` — how agents use `AiAdapter` and `ai.chat(...)`.
- `docs/config.md` — `LLM_DEFAULT_*` / `LLM_<AGENT>_*` environment variables.
