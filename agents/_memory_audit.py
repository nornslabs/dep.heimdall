"""Build the Urd's Well entries for memory commands: who asked, what happened, never the note.

An entry carries ids, the agent and a validated note key. The note's text and the raw command
are untrusted and stay out, so the audit log can be read without reading chat.
"""

from enum import StrEnum

from sqlmodel.ext.asyncio.session import AsyncSession

from agents._memory_commands import Forget, MemoryCommand, Remember
from heimdall.adapters.switch.agent.protocol import AgentMessage
from heimdall.modules.audit.api import (
    MAX_FIELD_LENGTH,
    AuditEntry,
    AuditScalar,
    append_in_transaction,
    pr_subject,
)
from heimdall.modules.memory.api import validate_key
from heimdall.modules.triage.api import PrSnapshot
from heimdall.shared.agents import AgentName
from heimdall.shared.context import PrRef, TenantCtx
from heimdall.shared.errors import InvalidNote

REMEMBERED_ACTION = "memory.remembered"
FORGOT_ACTION = "memory.forgot"
REFUSED_ACTION = "memory.refused"
UNKNOWN_ACTOR = "unknown"


class RefusalReason(StrEnum):
    """Why a memory command changed nothing."""

    NOT_A_MAINTAINER = "not_a_maintainer"
    REPO_NOT_ENABLED = "repo_not_enabled"
    OUTSIDE_THREAD = "outside_thread"
    INVALID = "invalid"
    CAP = "cap"
    NOT_FOUND = "not_found"


def memory_audit_entry(
    agent: AgentName,
    message: AgentMessage,
    pr: PrSnapshot | None,
    action: str,
    *,
    key: str | None = None,
    reason: RefusalReason | None = None,
) -> AuditEntry:
    """Describe one memory command outcome for the audit log.

    Args:
        agent: The agent whose notes the command was about.
        message: The command's message; only the sender and the ids are used.
        pr: The PR whose thread the command came from, or None when there was no PR to name.
        action: ``REMEMBERED_ACTION``, ``FORGOT_ACTION`` or ``REFUSED_ACTION``.
        key: The note key as the sender wrote it. It is kept only if it is a valid key.
        reason: Why the command was refused; only for ``REFUSED_ACTION``.

    Returns:
        An entry about the PR, or about the room when there is no PR.
    """
    payload: dict[str, AuditScalar] = {"agent": agent.value, "message_id": message.message_id}
    if pr is not None:
        payload["repo_id"] = pr.repo_id
    valid_key = _valid_key(key)
    if valid_key is not None:
        payload["key"] = valid_key
    if reason is not None:
        payload["reason"] = reason.value
    return AuditEntry(
        actor=message.sender_name.strip()[:MAX_FIELD_LENGTH] or UNKNOWN_ACTOR,
        action=action,
        subject=_subject(message, pr),
        payload=payload,
    )


def _subject(message: AgentMessage, pr: PrSnapshot | None) -> str:
    if pr is None:
        return f"room:{message.room_id}"
    return pr_subject(PrRef(repo_id=pr.repo_id, number=pr.number))


def _valid_key(key: str | None) -> str | None:
    if key is None:
        return None
    try:
        return validate_key(key)
    except InvalidNote:
        return None


async def record_memory_audit(
    session: AsyncSession,
    ctx: TenantCtx,
    agent: AgentName,
    message: AgentMessage,
    pr: PrSnapshot | None,
    action: str,
    *,
    key: str | None = None,
    reason: RefusalReason | None = None,
) -> None:
    """Record what happened to a note, in the same transaction as the change."""
    entry = memory_audit_entry(agent, message, pr, action, key=key, reason=reason)
    await append_in_transaction(session, ctx, entry)


async def record_refusal(
    session: AsyncSession,
    ctx: TenantCtx,
    agent: AgentName,
    message: AgentMessage,
    pr: PrSnapshot | None,
    command: MemoryCommand,
    reason: RefusalReason,
) -> None:
    """Record a refused note change; listing and help change nothing, so they record nothing."""
    if isinstance(command, Remember | Forget):
        await record_memory_audit(
            session, ctx, agent, message, pr, REFUSED_ACTION, key=command.key, reason=reason
        )


def denied_entry(
    agent: AgentName, message: AgentMessage, command: MemoryCommand
) -> AuditEntry | None:
    """The audit entry for a non-maintainer's attempt to change a note, if it is one."""
    if not isinstance(command, Remember | Forget):
        return None
    return memory_audit_entry(
        agent,
        message,
        None,
        REFUSED_ACTION,
        key=command.key,
        reason=RefusalReason.NOT_A_MAINTAINER,
    )
