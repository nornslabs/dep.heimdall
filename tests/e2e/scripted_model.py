"""A scripted model for Loki, so the replay never calls a real provider."""

from collections.abc import Sequence

from heimdall.adapters.ai import ChatMessage, ChatResponse, ToolChoice, ToolSpec, Usage

SCRIPTED_MODEL = "scripted-e2e"
NO_FINDINGS_REPLY = '{"findings": []}'


class ScriptedModel:
    """A ``ChatModel`` that always answers with one fixed reply, for Loki's single call."""

    def __init__(self, reply: str) -> None:
        """Keep the reply text the model returns; tests may replace ``reply`` between runs."""
        self.reply = reply
        self.calls: list[tuple[ChatMessage, ...]] = []

    @property
    def model(self) -> str:
        """The model name recorded in usage."""
        return SCRIPTED_MODEL

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        *,
        tool_choice: ToolChoice = "auto",
    ) -> ChatResponse:
        """Record the prompt and return the scripted reply."""
        self.calls.append(tuple(messages))
        return ChatResponse(self.reply, (), Usage(0, 0), SCRIPTED_MODEL)
