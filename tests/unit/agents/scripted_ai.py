"""A fake chat model that replays prepared replies and records what it was sent."""

from collections.abc import Sequence

from heimdall.adapters.ai import ChatMessage, ChatResponse, ToolCall, ToolChoice, ToolSpec, Usage


def text_reply(text: str, *, model: str = "scripted-1", usage: Usage | None = None) -> ChatResponse:
    return ChatResponse(text, (), usage or Usage(10, 2), model)


def tool_reply(*calls: ToolCall, model: str = "scripted-1") -> ChatResponse:
    return ChatResponse(None, calls, Usage(5, 1), model)


class ScriptedAi:
    """Return each scripted reply in order; fail loudly if the script runs out."""

    def __init__(
        self, replies: Sequence[ChatResponse | Exception], model: str = "scripted-1"
    ) -> None:
        self._replies = list(replies)
        self._model = model
        self.requests: list[tuple[tuple[ChatMessage, ...], tuple[ToolSpec, ...], ToolChoice]] = []

    @property
    def model(self) -> str:
        return self._model

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        *,
        tool_choice: ToolChoice = "auto",
    ) -> ChatResponse:
        self.requests.append((tuple(messages), tuple(tools), tool_choice))
        if not self._replies:
            raise AssertionError("ScriptedAi ran out of replies")
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
