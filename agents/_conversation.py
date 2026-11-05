"""Answer one mention: scope first, ask mode by default, act mode only when code allows it.

Ask mode always runs first, with read-only tools. Its reply says whether the message asks
the agent to act. Act mode, a second run with the agent's one write tool, opens only when
that reply is in scope, the sender is a maintainer, and a tool-less check that sees only the
maintainer's message agrees it asks to act. The model can ask to act; it can never grant
itself the tools. Nothing here posts; the caller queues the returned text through the outbox.
"""

from dataclasses import dataclass
from typing import Final

from agents._evidence import MentionMode
from agents._mention_prompt import MentionPr, Question
from agents._mention_run import (
    SUBMIT_INTENT_NAME,
    SUBMIT_REPLY_NAME,
    MentionAgent,
    MentionPorts,
    confirm_intent,
    run_mode,
)
from agents._reply import (
    ACT_DENIED_REPLY,
    FAILED_REPLY,
    NO_PR_REPLY,
    agent_prefix,
    render_agent_reply,
    render_unsummarised_actions,
)
from agents._runtime import ChatModel
from heimdall.shared.logging import get_logger

__all__ = [
    "MENTION_MAX_TURNS",
    "SUBMIT_INTENT_NAME",
    "SUBMIT_REPLY_NAME",
    "MentionAgent",
    "MentionAnswer",
    "MentionMode",
    "MentionPorts",
    "MentionPr",
    "Question",
    "answer_mention",
]

MENTION_MAX_TURNS: Final = 6

log = get_logger(__name__)


@dataclass(frozen=True)
class MentionAnswer:
    """The room message for a mention, and the PR whose thread it belongs in.

    Attributes:
        text: The rendered reply.
        pr_id: The PR the reply threads under, or None when the mention had no known PR.
    """

    text: str
    pr_id: str | None


async def answer_mention(
    agent: MentionAgent, ports: MentionPorts, ai: ChatModel, question: Question
) -> MentionAnswer:
    """Answer one mention and return the room message to post.

    Args:
        agent: The addressed agent's scope, prompt, tool sets and budget.
        ports: PR lookup, maintainer check, head check and tools.
        ai: The agent's model.
        question: The message and its sender.

    Returns:
        The rendered reply (an answer with evidence, a fixed redirect or refusal, or a fixed
        line when there is no PR or no grounded answer) and the PR it threads under.
    """
    pr = None if question.pr_ref is None else await ports.load_pr(question.pr_ref)
    if pr is None:
        return MentionAnswer(f"{agent_prefix(agent.scope)} {NO_PR_REPLY}", None)
    text = await _answer_for_pr(agent, ports, ai, pr, question)
    return MentionAnswer(text, pr.pr_id)


async def _answer_for_pr(
    agent: MentionAgent, ports: MentionPorts, ai: ChatModel, pr: MentionPr, question: Question
) -> str:
    """Run ask mode, then act mode only if every gate passes, and render what happened."""
    asked = (await run_mode(agent, ports, ai, pr, question, "ask")).reply
    if asked is None:
        return f"{agent_prefix(agent.scope)} {FAILED_REPLY}"
    if not (asked.in_scope and asked.wants_action):
        return render_agent_reply(agent.scope, asked)
    ids = {"agent": agent.scope.agent.value, "pr_id": pr.pr_id}
    if not await ports.is_maintainer(pr, question.sender):
        log.info("mention_act_refused", reason="not_a_maintainer", **ids)
        return f"{render_agent_reply(agent.scope, asked)}\n\n{ACT_DENIED_REPLY}"
    if not await confirm_intent(agent, ports, ai, pr, question):
        log.info("mention_act_refused", reason="message_did_not_ask", **ids)
        return render_agent_reply(agent.scope, asked)
    acted = await run_mode(agent, ports, ai, pr, question, "act")
    if acted.reply is not None:
        return render_agent_reply(agent.scope, acted.reply)
    if acted.actions:
        return render_unsummarised_actions(agent.scope, acted.actions)
    return f"{agent_prefix(agent.scope)} {FAILED_REPLY}"
