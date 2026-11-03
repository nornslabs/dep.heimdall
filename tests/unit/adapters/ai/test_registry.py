"""register_provider adds a provider that AiAdapter then routes chat calls to."""

from collections.abc import Iterator, Sequence

import httpx
import pytest
from pydantic import SecretStr

from heimdall.adapters.ai import (
    AiAdapter,
    AiConfig,
    ChatMessage,
    ChatResponse,
    ToolSpec,
    Usage,
)
from heimdall.adapters.ai.providers import PROVIDERS, register_provider
from heimdall.shared.errors import ConfigInvalid


class FakeProvider:
    def __init__(self) -> None:
        self.seen: list[tuple[str, ...]] = []

    async def send(
        self,
        http: httpx.AsyncClient,
        config: AiConfig,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
        tool_choice: str,
    ) -> ChatResponse:
        self.seen.append(tuple(message.content for message in messages))
        return ChatResponse("fake reply", (), Usage(3, 4), config.model)


@pytest.fixture
def fake_provider() -> Iterator[FakeProvider]:
    provider = FakeProvider()
    register_provider("fake", provider)
    yield provider
    PROVIDERS.pop("fake", None)


def _config(provider: str) -> AiConfig:
    return AiConfig(
        provider=provider, base_url="http://fake", api_key=SecretStr("k"), model="fake-1"
    )


async def test_adapter_routes_to_registered_provider(fake_provider: FakeProvider) -> None:
    ai = AiAdapter(_config("fake"))

    reply = await ai.chat([ChatMessage("user", "hello")])
    await ai.aclose()

    assert reply.text == "fake reply"
    assert reply.usage == Usage(3, 4)
    assert ai.model == "fake-1"
    assert fake_provider.seen == [("hello",)]


async def test_adapter_rejects_unregistered_provider() -> None:
    ai = AiAdapter(_config("missing"))

    with pytest.raises(ConfigInvalid, match="missing"):
        await ai.chat([ChatMessage("user", "hello")])
    await ai.aclose()


def test_builtin_providers_are_registered() -> None:
    assert {"openai_compat", "anthropic"} <= set(PROVIDERS)
