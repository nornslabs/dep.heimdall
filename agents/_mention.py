"""The ``agent.mention`` event: a maintainer's message to one agent, queued as a job.

The event only carries the message. Nothing here reads, parses or acts on ``text``; the agent
that receives the job (``agents._mention_task``) treats it as untrusted data.
"""

from dataclasses import dataclass
from typing import Final

from heimdall.adapters.db.columns import JsonObject
from heimdall.adapters.queue.enqueue import HEIMDALL_QUEUE, HUGINN_QUEUE, LOKI_QUEUE
from heimdall.shared.agents import AgentName

AGENT_MENTION_TASK: Final = "agent.mention"
# Only maintainers reach the event today. The field exists so a later change can let other
# roles in without changing the event's shape; act mode re-checks it either way.
SENDER_ROLE_MAINTAINER: Final = "maintainer"
_QUEUES: Final = {
    AgentName.HUGINN: HUGINN_QUEUE,
    AgentName.LOKI: LOKI_QUEUE,
    AgentName.HEIMDALL: HEIMDALL_QUEUE,
}


@dataclass(frozen=True)
class AgentMention:
    """One message that addressed one agent.

    Attributes:
        agent: The agent that was addressed.
        pr_ref: Id of the PR whose thread the message is in, or None outside a PR thread.
        room_id: Switch room the message was posted in; the reply goes back there.
        thread_id: Switch thread the message was posted in, or None for a room-level message.
        sender: The sender's name as Switch reports it (a GitHub login for a maintainer).
        sender_role: What the sender is allowed to do; ``SENDER_ROLE_MAINTAINER`` for now.
        text: The message body. Untrusted data: never log it and never act on it.
        message_id: Switch's id for the message; one message gives one event per agent.
    """

    agent: AgentName
    pr_ref: str | None
    room_id: str
    thread_id: str | None
    sender: str
    sender_role: str
    text: str
    message_id: str

    def to_job_args(self, installation_id: int) -> JsonObject:
        """Build the queue job's keyword arguments for this event.

        Args:
            installation_id: Tenant the room belongs to; every job carries one.

        Returns:
            JSON-ready arguments matching the ``agent.mention`` task's signature.
        """
        return {
            "installation_id": installation_id,
            "agent": self.agent.value,
            "pr_ref": self.pr_ref,
            "room_id": self.room_id,
            "thread_id": self.thread_id,
            "sender": self.sender,
            "sender_role": self.sender_role,
            "text": self.text,
            "message_id": self.message_id,
        }


def mention_queue(agent: AgentName) -> str:
    """Return the queue only this agent's worker claims.

    Args:
        agent: The agent that was addressed.

    Returns:
        The queue name its job goes to.
    """
    return _QUEUES[agent]


def mention_lock(agent: AgentName, message_id: str) -> str:
    """Name the lock that keeps one waiting job per message and agent.

    Args:
        agent: The agent that was addressed.
        message_id: Switch's id for the message.

    Returns:
        A queueing lock; a second job with the same value is refused while the first waits.
    """
    return f"mention:{agent.value}:{message_id}"
