"""One mention run in ask or act mode, and the tool-less check that the message asks to act.

Every tool in a mention run is pinned to the mention's PR and tenant: whatever ids the model
passes, the tool gets the real ones, so neither the model nor PR text can point a tool at
another tenant's data.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final, Protocol

from pydantic import BaseModel, ConfigDict

from agents._evidence import EvidenceTracker, MentionMode, reply_problems
from agents._mention_prompt import (
    MentionPr,
    Question,
    build_intent_messages,
    build_mention_messages,
)
from agents._reply import ActionTaken, AgentReply
from agents._runtime import (
    AgentBudget,
    AgentRun,
    AgentTool,
    ChatModel,
    Completed,
    SubmitTool,
    ToolBox,
    run_agent,
)
from agents._scope import AgentScope
from heimdall.shared.logging import get_logger

SUBMIT_REPLY_NAME: Final = "submit_reply"
SUBMIT_INTENT_NAME: Final = "submit_intent"
INTENT_MAX_TURNS: Final = 2
_REPLY_DESCRIPTION: Final = (
    "Finish with your reply: the answer and its evidence, or in_scope=false with a redirect."
)
_INTENT_DESCRIPTION: Final = "Say whether the maintainer's message asks you to act."

log = get_logger(__name__)


@dataclass(frozen=True)
class MentionAgent:
    """One agent's conversation setup.

    Attributes:
        scope: What it answers, redirects and refuses.
        instructions: Its conversation role prompt.
        ask_tools: Read-only tools offered in ask mode.
        act_tools: Ask tools plus the one write tool act mode opens.
        budget: Turn, token and call limits for each run.
    """

    scope: AgentScope
    instructions: str
    ask_tools: frozenset[str]
    act_tools: frozenset[str]
    budget: AgentBudget


class MentionPorts(Protocol):
    """What answering a mention needs from outside: the PR, the maintainer list and tools."""

    async def load_pr(self, pr_ref: str) -> MentionPr | None:
        """Return the PR, or None when it is not registered for this tenant."""
        ...

    async def is_maintainer(self, pr: MentionPr, sender: str) -> bool:
        """Say whether ``sender`` is on the PR repository's maintainer list."""
        ...

    async def read_head(self, pr: MentionPr) -> str:
        """Read the PR's head now, so an answer for a moved head is dropped."""
        ...

    async def tools(self, pr: MentionPr, mode: MentionMode) -> Sequence[AgentTool]:
        """Return the agent's tools for this PR; write tools come with their guards."""
        ...


@dataclass(frozen=True)
class ModeResult:
    """How one mention run ended.

    Attributes:
        reply: The checked reply, or None when the run did not finish.
        actions: The writes that actually ran, whether or not the run finished.
    """

    reply: AgentReply | None
    actions: frozenset[ActionTaken]


class ActIntent(BaseModel):
    """The intent check's answer: does the message ask the agent to act?"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    wants_action: bool


async def run_mode(
    agent: MentionAgent,
    ports: MentionPorts,
    ai: ChatModel,
    pr: MentionPr,
    question: Question,
    mode: MentionMode,
) -> ModeResult:
    """Run the agent once in ``mode`` and return its checked reply and the writes that ran.

    Only tools on the mode's list are offered, whatever the ports hand back, so ask mode can
    never see a write tool.

    Args:
        agent: The agent's scope, prompt, tool sets and budget.
        ports: PR head check and tools.
        ai: The agent's model.
        pr: The PR the mention is about; every tool call is pinned to it.
        question: The message and its sender.
        mode: ``ask`` (read-only) or ``act`` (plus the agent's one write tool).

    Returns:
        The reply (None when the run did not finish) and the writes that ran.
    """
    allowed = agent.act_tools if mode == "act" else agent.ask_tools
    tracker = EvidenceTracker()
    pinned = _pinned_ids(pr)
    offered = [
        tracker.wrap(_pin(tool, pinned))
        for tool in await ports.tools(pr, mode)
        if tool.spec.name in allowed
    ]
    name = agent.scope.agent

    async def check(reply: AgentReply) -> tuple[str, ...]:
        return reply_problems(reply, tracker, agent=name, mode=mode)

    run = AgentRun(
        agent=name.value,
        ai=ai,
        toolbox=ToolBox(allowed, offered),
        submit=SubmitTool(SUBMIT_REPLY_NAME, AgentReply, _REPLY_DESCRIPTION, cross_check=check),
        budget=agent.budget,
        head_sha=pr.head_sha,
        read_current_head=lambda: ports.read_head(pr),
    )
    messages = build_mention_messages(agent.instructions, agent.scope, pr, question, mode)
    outcome = await run_agent(run, messages)
    if isinstance(outcome, Completed):
        return ModeResult(outcome.payload, tracker.actions())
    log.info(
        "mention_not_answered",
        agent=name.value,
        pr_id=pr.pr_id,
        mode=mode,
        reason=type(outcome).__name__,
    )
    return ModeResult(None, tracker.actions())


async def confirm_intent(
    agent: MentionAgent, ports: MentionPorts, ai: ChatModel, pr: MentionPr, question: Question
) -> bool:
    """Ask, with no tools and no PR data, whether the message itself asks the agent to act.

    The ask run has read PR content, so its ``wants_action`` could come from text in the PR.
    Act mode opens only when this check, which sees the maintainer's message alone, agrees.

    Args:
        agent: The agent's scope, tool sets and budget.
        ports: PR head check.
        ai: The agent's model.
        pr: The PR the mention is about.
        question: The maintainer's message.

    Returns:
        True only when the check finished and said the message asks to act.
    """
    run = AgentRun(
        agent=agent.scope.agent.value,
        ai=ai,
        toolbox=ToolBox(frozenset(), ()),
        submit=SubmitTool(SUBMIT_INTENT_NAME, ActIntent, _INTENT_DESCRIPTION),
        budget=replace(agent.budget, max_turns=INTENT_MAX_TURNS),
        head_sha=pr.head_sha,
        read_current_head=lambda: ports.read_head(pr),
    )
    writes = agent.act_tools - agent.ask_tools
    outcome = await run_agent(run, build_intent_messages(agent.scope, writes, question))
    return isinstance(outcome, Completed) and outcome.payload.wants_action


def _pinned_ids(pr: MentionPr) -> Mapping[str, object]:
    """Return the tool arguments that must always name the mention's own PR and tenant."""
    return {
        "installation_id": pr.installation_id,
        "pr_id": pr.pr_id,
        "repo_id": pr.repo_id,
        "repo_full_name": pr.repo_full_name,
        "pr_number": pr.number,
        "head_sha": pr.head_sha,
    }


def _pin(tool: AgentTool, pinned: Mapping[str, object]) -> AgentTool:
    """Wrap a tool so every pinned argument it is given is replaced with the real value."""
    original = tool.handler

    async def handler(arguments: Mapping[str, object]) -> object:
        fixed = {key: pinned[key] for key in arguments if key in pinned}
        return await original({**arguments, **fixed})

    return AgentTool(tool.spec, handler)
