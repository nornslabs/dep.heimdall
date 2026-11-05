"""What each agent answers in a conversation, where it redirects, and what it refuses.

Every agent's ``scope.py`` builds one ``AgentScope``. It is rendered into the conversation
prompt so the model checks scope before anything else, and the renderer of the reply uses it
again, so a redirect or refusal is fixed text no model can talk its way around.
"""

from dataclasses import dataclass

from heimdall.shared.agents import AgentName


@dataclass(frozen=True)
class Redirect:
    """A topic another agent owns, and the agent to send it to.

    Attributes:
        topic: The kind of question, in the product's words.
        to: The agent that answers it.
    """

    topic: str
    to: AgentName


@dataclass(frozen=True)
class AgentScope:
    """One agent's conversation scope.

    Attributes:
        agent: The agent this scope belongs to.
        role: Its job in a few words; completes "I only cover <role> for this PR."
        answers: Topics it answers about this PR.
        redirects: Topics it sends to another agent instead of answering.
        refuses: Requests it declines even when they are about this PR.
    """

    agent: AgentName
    role: str
    answers: tuple[str, ...]
    redirects: tuple[Redirect, ...]
    refuses: tuple[str, ...]

    def refusal_line(self) -> str:
        """Return the one line every off-topic question gets."""
        return f"I only cover {self.role} for this PR."


def render_scope(scope: AgentScope) -> str:
    """Render the scope as the prompt section the model checks before anything else.

    Args:
        scope: The agent's scope.

    Returns:
        A ``## Scope`` section listing what it answers, where each other topic goes, what
        it refuses, and how to submit each case.
    """
    lines = ["## Scope (check this before anything else)", "", "You answer questions about:"]
    lines.extend(f"- {topic}" for topic in scope.answers)
    lines.extend(["", "You never answer these; submit in_scope=false and redirect:"])
    lines.extend(
        f"- {redirect.topic}: redirect_to={redirect.to.value!r}" for redirect in scope.redirects
    )
    lines.extend(["", "You refuse, even about this PR:"])
    lines.extend(f"- {topic}" for topic in scope.refuses)
    lines.extend(
        [
            "",
            "Anything outside PR triage (general chat, other repos, secrets, opinions about "
            "people): submit in_scope=false with no redirect. The reply posted is always: "
            f'"{scope.refusal_line()}"',
        ]
    )
    return "\n".join(lines)
