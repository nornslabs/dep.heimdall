"""Pure domain logic of the audit module (no I/O)."""

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from heimdall.shared.context import PrRef

MAX_FIELD_LENGTH = 200
# Entries carry ids and hashes, never PR text; a small cap keeps diffs and bodies out.
MAX_PAYLOAD_BYTES = 4_000

type AuditScalar = str | int | float | bool | None
type AuditPayload = Mapping[str, AuditScalar]


def pr_subject(pr_ref: PrRef) -> str:
    """Name a pull request as an audit subject, so one PR's trail is one indexed lookup.

    Args:
        pr_ref: Repository id and PR number.

    Returns:
        The subject string, for example ``pr:42#7``.
    """
    return f"pr:{pr_ref.repo_id}#{pr_ref.number}"


@dataclass(frozen=True)
class AuditEntry:
    """One thing that happened, ready to be appended to the audit log.

    Attributes:
        actor: Who did it: a GitHub login or a policy such as ``policy:auto_suggest``.
        action: What happened, for example ``draft.approved``.
        subject: What it happened to; use ``pr_subject`` for PR events.
        payload: Flat ids, hashes and flags. Never PR text, diffs or secrets.
    """

    actor: str
    action: str
    subject: str
    payload: AuditPayload = field(default_factory=dict[str, AuditScalar])

    def __post_init__(self) -> None:
        """Reject entries that would be unreadable or would smuggle PR content into the log.

        Raises:
            ValueError: A field is blank or too long, a payload key is blank, or the
                payload is over ``MAX_PAYLOAD_BYTES``.
        """
        for name, value in (
            ("actor", self.actor),
            ("action", self.action),
            ("subject", self.subject),
        ):
            if not value.strip():
                raise ValueError(f"audit {name} cannot be blank")
            if len(value) > MAX_FIELD_LENGTH:
                raise ValueError(f"audit {name} is longer than {MAX_FIELD_LENGTH} characters")
        if any(not key.strip() for key in self.payload):
            raise ValueError("audit payload keys cannot be blank")
        if len(json.dumps(dict(self.payload)).encode()) > MAX_PAYLOAD_BYTES:
            raise ValueError(f"audit payload is larger than {MAX_PAYLOAD_BYTES} bytes")


@dataclass(frozen=True)
class AuditView:
    """One saved audit log entry, as other modules and the Desk read it.

    Attributes:
        id: Row id; increases with insert order.
        at: When the entry was written.
        actor: Who did it.
        action: What happened.
        subject: What it happened to.
        payload: The ids and hashes saved with it.
    """

    id: int
    at: datetime
    actor: str
    action: str
    subject: str
    payload: AuditPayload


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
