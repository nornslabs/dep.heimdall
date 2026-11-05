"""How an agent run ended, and what it cost.

The runtime returns one of these to its caller and never posts anything itself; the caller
turns the outcome into a room card through the outbox.
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from heimdall.adapters.ai import ChatResponse, Usage
from heimdall.modules.audit.api import UsageRecord

NO_USAGE = Usage(0, 0)

CouldNotFinishReason = Literal["turn_limit", "token_budget", "invalid_submission"]


@dataclass(frozen=True)
class RunUsage:
    """Tokens summed over one agent run and the model the provider said answered."""

    usage: Usage = NO_USAGE
    model: str | None = None

    @property
    def tokens(self) -> int:
        """Input plus output tokens, the figure the run token budget is checked against."""
        return self.usage.input_tokens + self.usage.output_tokens

    def add(self, reply: ChatResponse) -> "RunUsage":
        """Return new totals that include one more reply."""
        return RunUsage(self.usage + reply.usage, reply.model or self.model)

    def to_record(
        self, agent: str, pr_id: UUID | None, *, fallback_model: str = "unknown"
    ) -> UsageRecord:
        """Build the audit row for this run, using the configured model if none answered."""
        return UsageRecord(
            agent=agent,
            model=self.model or fallback_model,
            input_tokens=self.usage.input_tokens,
            output_tokens=self.usage.output_tokens,
            pr_id=pr_id,
        )


@dataclass(frozen=True)
class Completed[T]:
    """The model submitted a valid answer for the head the run started on."""

    payload: T
    usage: RunUsage
    turns: int


@dataclass(frozen=True)
class CouldNotFinish:
    """The run hit a turn, token or repair limit before a valid submission."""

    reason: CouldNotFinishReason
    usage: RunUsage
    turns: int


@dataclass(frozen=True)
class Unavailable:
    """The model or a tool stayed down through every retry; ``reason`` is the error type."""

    reason: str
    usage: RunUsage
    turns: int


@dataclass(frozen=True)
class Stale:
    """The PR head moved during the run, so the answer was dropped."""

    usage: RunUsage
    turns: int


type RunOutcome[T] = Completed[T] | CouldNotFinish | Unavailable | Stale
