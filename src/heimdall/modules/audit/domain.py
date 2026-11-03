"""Pure domain logic of the audit module (no I/O)."""

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class UsageRecord:
    """Tokens one agent run spent with one model, ready to be saved for cost reporting.

    Attributes:
        agent: Agent name, for example ``loki``.
        model: Model string the provider reported, not just the one requested.
        input_tokens: Prompt tokens summed over the run.
        output_tokens: Completion tokens summed over the run.
        pr_id: Pull request the run worked on, when there was one.
    """

    agent: str
    model: str
    input_tokens: int
    output_tokens: int
    pr_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        """Reject records that would make cost reports wrong.

        Raises:
            ValueError: agent or model is blank, or a token count is negative.
        """
        if not self.agent.strip() or not self.model.strip():
            raise ValueError("usage needs an agent and a model")
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("token counts cannot be negative")
