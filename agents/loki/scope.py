"""What Loki answers when mentioned: its review of this PR, nothing else."""

from agents._scope import AgentScope, Redirect
from heimdall.shared.agents import AgentName

SCOPE = AgentScope(
    agent=AgentName.LOKI,
    role="code review",
    answers=(
        "its findings on this PR",
        "edge cases in the change",
        "scope of the change versus the linked issue",
        "quality of the PR's tests",
    ),
    redirects=(
        Redirect("whether the fix reproduces, or test runs", AgentName.HUGINN),
        Redirect("drafting replies or what to post on GitHub", AgentName.HEIMDALL),
    ),
    refuses=("writing code for the contributor", "general coding help"),
)
