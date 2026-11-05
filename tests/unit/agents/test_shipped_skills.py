"""Every shipped skill must validate against its agent's real allow-list."""

import pytest

from agents._skills import load_skills
from agents.heimdall.allowed_tools import ALLOWED_TOOLS as HEIMDALL_TOOLS
from agents.heimdall.config import REQUIRED_SKILLS as HEIMDALL_REQUIRED
from agents.huginn.allowed_tools import ALLOWED_TOOLS as HUGINN_TOOLS
from agents.huginn.config import REQUIRED_SKILLS as HUGINN_REQUIRED
from agents.loki.allowed_tools import ALLOWED_TOOLS as LOKI_TOOLS
from agents.loki.config import REQUIRED_SKILLS as LOKI_REQUIRED
from heimdall.shared.agents import AgentName

EXPECTED = {
    AgentName.HUGINN: (
        HUGINN_TOOLS,
        HUGINN_REQUIRED,
        {"pick_test_command", "judge_repro", "fix_setup_failure"},
    ),
    AgentName.LOKI: (
        LOKI_TOOLS,
        LOKI_REQUIRED,
        {"edge_cases", "scope_check", "test_quality", "mechanical_tagging"},
    ),
    AgentName.HEIMDALL: (
        HEIMDALL_TOOLS,
        HEIMDALL_REQUIRED,
        {
            "write_card",
            "draft_change_request",
            "draft_thanks",
            "draft_close",
            "explain_why",
            "mechanical_task",
        },
    ),
}


@pytest.mark.parametrize("agent", list(AgentName))
def test_shipped_skills_validate_for_every_agent(agent: AgentName) -> None:
    allowed, required, expected = EXPECTED[agent]

    skills = load_skills(agent, allowed, required)

    assert set(skills.names()) == expected
    assert len(skills.prompt_index().splitlines()) == len(expected) + 2
    for name in expected:
        body = skills.load(name)
        assert body is not None
        assert body.strip()


@pytest.mark.parametrize("agent", list(AgentName))
def test_prompt_index_omits_playbook_text(agent: AgentName) -> None:
    allowed, required, _ = EXPECTED[agent]
    skills = load_skills(agent, allowed, required)

    index = skills.prompt_index()

    for skill in skills.skills:
        assert skill.body not in index
        assert skill.when not in index
