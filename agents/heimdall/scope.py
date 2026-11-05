"""What Heimdall answers when mentioned: the triage desk for this PR, nothing else."""

from agents._scope import AgentScope, Redirect
from heimdall.shared.agents import AgentName

SCOPE = AgentScope(
    agent=AgentName.HEIMDALL,
    role="the triage desk",
    answers=(
        "the card for this PR",
        "intake rules and why they passed or failed",
        "drafts waiting for approval",
        "next steps for the maintainer",
    ),
    redirects=(
        Redirect("deep test questions: repro runs, commands, verdicts", AgentName.HUGINN),
        Redirect("deep review questions: findings, edge cases, code", AgentName.LOKI),
    ),
    refuses=("posting anything to GitHub without the maintainer's 👍 approval",),
)
