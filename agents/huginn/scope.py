"""What Huginn answers when mentioned: this PR's tests and reproductions, nothing else."""

from agents._scope import AgentScope, Redirect
from heimdall.shared.agents import AgentName

SCOPE = AgentScope(
    agent=AgentName.HUGINN,
    role="this PR's tests and reproductions",
    answers=(
        "repro runs for this PR and their results",
        "test commands and how they were picked",
        "why a verdict was given",
        "flaky or setup failures",
    ),
    redirects=(
        Redirect("code review questions: findings, edge cases, scope", AgentName.LOKI),
        Redirect("what to post on GitHub and next steps for the maintainer", AgentName.HEIMDALL),
    ),
    refuses=("anything not about this PR's tests",),
)
