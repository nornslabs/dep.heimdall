"""Each agent's scope: what it answers, where it redirects, and what it refuses."""

import pytest

from agents._scope import AgentScope, render_scope
from agents.heimdall.scope import SCOPE as HEIMDALL_SCOPE
from agents.huginn.scope import SCOPE as HUGINN_SCOPE
from agents.loki.scope import SCOPE as LOKI_SCOPE
from heimdall.shared.agents import AgentName

ALL_SCOPES = (HUGINN_SCOPE, LOKI_SCOPE, HEIMDALL_SCOPE)


@pytest.mark.parametrize("scope", ALL_SCOPES, ids=lambda scope: scope.agent.value)
def test_the_rendered_scope_names_the_role_redirects_and_refusal(scope: AgentScope) -> None:
    text = render_scope(scope)

    assert scope.refusal_line() in text
    for topic in scope.answers:
        assert topic in text
    for redirect in scope.redirects:
        assert redirect.topic in text
        assert f"redirect_to={redirect.to.value!r}" in text


@pytest.mark.parametrize("scope", ALL_SCOPES, ids=lambda scope: scope.agent.value)
def test_no_agent_redirects_to_itself(scope: AgentScope) -> None:
    assert all(redirect.to != scope.agent for redirect in scope.redirects)


@pytest.mark.parametrize("scope", ALL_SCOPES, ids=lambda scope: scope.agent.value)
def test_the_refusal_is_one_line_naming_the_role(scope: AgentScope) -> None:
    assert scope.refusal_line() == f"I only cover {scope.role} for this PR."


def test_each_agent_redirects_where_the_issue_says() -> None:
    assert {r.to for r in HUGINN_SCOPE.redirects} == {AgentName.LOKI, AgentName.HEIMDALL}
    assert {r.to for r in LOKI_SCOPE.redirects} == {AgentName.HUGINN, AgentName.HEIMDALL}
    assert {r.to for r in HEIMDALL_SCOPE.redirects} == {AgentName.HUGINN, AgentName.LOKI}


def test_each_scope_belongs_to_its_agent() -> None:
    assert [scope.agent for scope in ALL_SCOPES] == [
        AgentName.HUGINN,
        AgentName.LOKI,
        AgentName.HEIMDALL,
    ]
