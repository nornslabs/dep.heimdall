# AI adapter

This adapter is the only provider-facing model boundary. It sends plain HTTP
requests through `httpx` and normalizes provider responses into typed messages,
tool calls, and token usage.

| File | Role |
|---|---|
| `adapter.py` | `AiAdapter` and provider registry |
| `config.py` | Immutable endpoint and model settings |
| `errors.py` | Retryable availability and malformed-response errors |
| `providers.py` | OpenAI-compatible and Anthropic HTTP translations |
| `types.py` | Provider-neutral request and response values |

Register another provider with `register_provider(name, implementation)`. The
adapter owns its HTTP client unless a caller supplies one.
