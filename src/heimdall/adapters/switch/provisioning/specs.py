"""The three Heimdall agents as Switch registers them."""

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class AgentSpec:
    """What Switch needs to know about one agent when it is registered.

    Attributes:
        name: Agent name; also the @-mention handle. Lower case.
        description: One sentence shown in the Gateway and to people in the room.
    """

    name: str
    description: str


AGENT_SPECS: Final = (
    AgentSpec(
        name="huginn",
        description="Runs the repository's tests on a PR and reports whether the bug reproduces.",
    ),
    AgentSpec(
        name="loki",
        description="Reads a PR's diff and questions it, citing file and line for every finding.",
    ),
    AgentSpec(
        name="heimdall",
        description="Assembles the review card and the draft a maintainer approves before GitHub.",
    ),
)
