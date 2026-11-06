"""``AgentReply``: an agent's answer to a mention, and how it is rendered for the room.

The schema rejects an answer about the PR that cites no evidence. Whether the cited evidence
is real is known only to the tools, so ``agents._evidence`` checks that after validation.
Redirects and refusals render as fixed text; the model's wording is never posted for them.
"""

import re
from collections.abc import Callable, Iterable, Mapping
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents._scope import AgentScope

MAX_ANSWER_CHARS: Final = 1200
MAX_EVIDENCE: Final = 10
MAX_EVIDENCE_CHARS: Final = 200

NO_PR_REPLY: Final = "Mention me in a PR's thread and I'll answer about that PR."
FAILED_REPLY: Final = "I couldn't produce a grounded answer to that. Try asking more narrowly."
ACT_DENIED_REPLY: Final = "Only maintainers can ask me to act; I've answered without acting."

ActionTaken = Literal["none", "repro_started", "findings_saved", "draft_saved", "intake_rerun"]
AgentHandle = Literal["huginn", "loki", "heimdall"]

SELF_EVIDENT_ACTIONS: Final[frozenset[ActionTaken]] = frozenset({"intake_rerun"})
"""Actions whose tool returns nothing a reply could cite; the tool having run is the evidence."""

ACTION_DONE: Final[Mapping[ActionTaken, str]] = {
    "repro_started": "started a repro",
    "findings_saved": "saved findings",
    "draft_saved": "saved a draft for your approval",
    "intake_rerun": "re-ran intake",
}
"""How a reply names a write that ran, when the model could not summarise it."""

# "src/pager.py:91", "src/pager.py:91-99": the path has a letter or digit and a "." or "/".
_FILE_LINE: Final = re.compile(r"(?P<token>(?=[^\s:]*[A-Za-z0-9])[^\s:]*[./][^\s:]*):\d+(?:-\d+)?")
# "finding:F-12", "repro:R-3": ids of at least three characters.
_ARTIFACT: Final = re.compile(r"(?:finding|repro):(?P<token>[A-Za-z0-9][A-Za-z0-9_-]{2,})")
_BROADCAST: Final = re.compile(r"@(channel|all|here|everyone)\b", re.IGNORECASE)


class AgentReply(BaseModel):
    """What an agent submits to answer a mention.

    Attributes:
        in_scope: False when the question belongs to another agent or to no agent.
        redirect_to: The agent that owns an out-of-scope question, if one does.
        answer: The answer; ignored for out-of-scope replies, which render fixed text.
        evidence: ``path:line``, ``finding:<id>`` or ``repro:<id>`` backing the answer.
        action_taken: The write the agent made in act mode; ``none`` in ask mode.
        wants_action: True when the message asks the agent to act, not just to answer.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    in_scope: bool
    redirect_to: AgentHandle | None = None
    answer: str = Field(max_length=MAX_ANSWER_CHARS)
    evidence: tuple[str, ...] = Field(default=(), max_length=MAX_EVIDENCE)
    action_taken: ActionTaken = "none"
    wants_action: bool = False

    @model_validator(mode="after")
    def _grounded_and_consistent(self) -> Self:
        """Require evidence for a PR claim, and nothing but a redirect or refusal otherwise."""
        bad = [entry for entry in self.evidence if _match(entry) is None]
        if bad:
            raise ValueError("evidence entries must be path:line, finding:<id> or repro:<id>")
        if self.in_scope:
            return self._check_in_scope()
        if self.action_taken != "none":
            raise ValueError("action_taken must be 'none' when in_scope is false")
        if self.wants_action:
            raise ValueError("wants_action must be false when in_scope is false")
        return self

    def _check_in_scope(self) -> Self:
        """An in-scope reply answers, cites evidence, and redirects nowhere.

        A request to act is not a claim about the PR, so it may come without evidence. Nor is
        a self-evident action: ``reply_problems`` still refuses it unless that tool ran.
        """
        if self.redirect_to is not None:
            raise ValueError("redirect_to must be null when in_scope is true")
        if not self.answer.strip():
            raise ValueError("answer is required when in_scope is true")
        if not self.evidence and not self.wants_action and not self._acted_self_evidently():
            raise ValueError("evidence: an answer about the PR needs at least one entry")
        return self

    def _acted_self_evidently(self) -> bool:
        """Say whether the reply reports an action that leaves nothing to cite."""
        return self.action_taken in SELF_EVIDENT_ACTIONS

    def filtered(self, clean: Callable[[str], str]) -> Self:
        """Return a copy with the shared tone filter applied to the answer.

        Args:
            clean: The tone filter; returns an empty string when nothing about the code is left.

        Returns:
            A copy whose answer has been cleaned; evidence and flags are unchanged.
        """
        return self.model_copy(update={"answer": clean(self.answer)})


def evidence_token(entry: str) -> str:
    """Return the part of an evidence entry a tool must have returned: a path or an id.

    Args:
        entry: One validated evidence entry.

    Returns:
        The file path for ``path:line``, the id for ``finding:<id>`` and ``repro:<id>``.

    Raises:
        ValueError: ``entry`` is not a valid evidence entry.
    """
    match = _match(entry)
    if match is None:
        raise ValueError("not an evidence entry")
    return match.group("token")


def render_agent_reply(scope: AgentScope, reply: AgentReply) -> str:
    """Render a validated reply as the room message, prefixed with the agent's name.

    Args:
        scope: The replying agent's scope, for its name and refusal line.
        reply: The checked, tone-filtered reply.

    Returns:
        A fixed redirect or refusal line when out of scope; otherwise the answer (with room
        broadcasts neutralised) and its evidence, or the failure line when no answer is left.
    """
    prefix = agent_prefix(scope)
    if not reply.in_scope and reply.redirect_to is not None:
        return f"{prefix} that's a question for @{reply.redirect_to}; ask them in this thread."
    if not reply.in_scope:
        return f"{prefix} {scope.refusal_line()}"
    answer = _BROADCAST.sub(r"\1", reply.answer).strip()
    if not answer:
        return f"{prefix} {FAILED_REPLY}"
    if not reply.evidence:
        return f"{prefix} {answer}"
    cited = ", ".join(f"`{entry}`" for entry in reply.evidence)
    return f"{prefix} {answer}\n\nEvidence: {cited}"


def render_unsummarised_actions(scope: AgentScope, actions: Iterable[ActionTaken]) -> str:
    """Tell the room which writes ran when the act run could not finish its reply.

    Args:
        scope: The replying agent's scope, for its name.
        actions: The writes that ran.

    Returns:
        A fixed line naming each write, so a write is never reported as nothing happening.
    """
    done = " and ".join(ACTION_DONE[action] for action in sorted(actions))
    return f"{agent_prefix(scope)} I {done}, but couldn't write a grounded summary of it."


def agent_prefix(scope: AgentScope) -> str:
    """Return the ``Name:`` prefix every reply from this agent starts with."""
    return f"{scope.agent.value.capitalize()}:"


def _match(entry: str) -> re.Match[str] | None:
    """Match a length-capped evidence entry against the artifact form, then ``path:line``."""
    if len(entry) > MAX_EVIDENCE_CHARS:
        return None
    return _ARTIFACT.fullmatch(entry) or _FILE_LINE.fullmatch(entry)
