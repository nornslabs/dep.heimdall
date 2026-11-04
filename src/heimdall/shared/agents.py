"""The names of Heimdall's three agents, shared by whatever writes or routes their posts."""

from enum import StrEnum


class AgentName(StrEnum):
    """An agent's name as it appears in outbox payloads and ``SWITCH_<NAME>_*`` variables."""

    HUGINN = "huginn"
    LOKI = "loki"
    HEIMDALL = "heimdall"
