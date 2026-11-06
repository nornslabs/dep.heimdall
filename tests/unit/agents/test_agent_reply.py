"""AgentReply: the answer to a mention, and the rules that keep it grounded and on-topic."""

import pytest
from pydantic import ValidationError

from agents._reply import (
    FAILED_REPLY,
    MAX_ANSWER_CHARS,
    AgentReply,
    evidence_token,
    render_agent_reply,
    render_unsummarised_actions,
)
from agents.huginn.scope import SCOPE as HUGINN_SCOPE
from agents.loki.scope import SCOPE as LOKI_SCOPE


def _reply(**overrides: object) -> AgentReply:
    fields: dict[str, object] = {
        "in_scope": True,
        "answer": "The repro failed at setup: pip could not resolve pager.",
        "evidence": ["repro:R-3"],
    }
    return AgentReply.model_validate({**fields, **overrides})


def test_an_answer_about_the_pr_without_evidence_fails_validation() -> None:
    with pytest.raises(ValidationError, match="evidence"):
        _reply(evidence=[])


def test_a_request_to_act_needs_no_evidence() -> None:
    reply = _reply(answer="I'll rerun it.", evidence=[], wants_action=True)

    assert reply.wants_action


def test_an_in_scope_answer_with_evidence_is_accepted() -> None:
    reply = _reply(evidence=["src/pager.py:91", "finding:F-12", "repro:R-3"])

    assert reply.evidence == ("src/pager.py:91", "finding:F-12", "repro:R-3")


@pytest.mark.parametrize(
    "entry",
    [
        "somewhere",
        "src/pager.py",
        "finding:",
        "repro: R-3",
        "src/a.py:x",
        "",
        "a:1",
        "1:2",
        "./:3",
        "finding:1",
        "src/" + "a" * 300 + ".py:1",
    ],
)
def test_evidence_must_name_a_file_line_finding_or_repro(entry: str) -> None:
    with pytest.raises(ValidationError, match="evidence"):
        _reply(evidence=[entry])


def test_a_file_range_counts_as_evidence() -> None:
    assert _reply(evidence=["src/pager.py:91-99"]).evidence == ("src/pager.py:91-99",)


def test_an_out_of_scope_reply_needs_no_evidence() -> None:
    reply = _reply(in_scope=False, redirect_to="loki", answer="", evidence=[])

    assert reply.redirect_to == "loki"


def test_an_out_of_scope_reply_cannot_claim_an_action() -> None:
    with pytest.raises(ValidationError, match="action_taken"):
        _reply(in_scope=False, answer="", evidence=[], action_taken="repro_started")


def test_an_out_of_scope_reply_cannot_ask_to_act() -> None:
    with pytest.raises(ValidationError, match="wants_action"):
        _reply(in_scope=False, answer="", evidence=[], wants_action=True)


def test_an_in_scope_reply_cannot_also_redirect() -> None:
    with pytest.raises(ValidationError, match="redirect_to"):
        _reply(redirect_to="loki")


def test_an_in_scope_reply_needs_an_answer() -> None:
    with pytest.raises(ValidationError, match="answer"):
        _reply(answer="   ")


def test_an_answer_longer_than_the_limit_is_rejected() -> None:
    with pytest.raises(ValidationError, match="answer"):
        _reply(answer="x" * (MAX_ANSWER_CHARS + 1))


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="post_to_github"):
        _reply(post_to_github=True)


def test_redirect_must_name_one_of_the_three_agents() -> None:
    with pytest.raises(ValidationError, match="redirect_to"):
        _reply(in_scope=False, redirect_to="gjallarhorn", answer="", evidence=[])


@pytest.mark.parametrize(
    ("entry", "token"),
    [("src/pager.py:91", "src/pager.py"), ("finding:F-12", "F-12"), ("repro:R-3", "R-3")],
)
def test_evidence_token_is_the_part_a_tool_must_have_returned(entry: str, token: str) -> None:
    assert evidence_token(entry) == token


def test_the_tone_filter_cleans_only_the_answer() -> None:
    reply = _reply(answer="noisy")

    cleaned = reply.filtered(lambda text: text.upper())

    assert cleaned.answer == "NOISY"
    assert cleaned.evidence == reply.evidence


def test_an_in_scope_reply_renders_the_answer_and_its_evidence() -> None:
    message = render_agent_reply(HUGINN_SCOPE, _reply(evidence=["repro:R-3", "src/a.py:4"]))

    assert message.startswith("Huginn: The repro failed at setup")
    assert "`repro:R-3`" in message
    assert "`src/a.py:4`" in message


def test_a_redirect_renders_a_fixed_line_and_never_the_models_answer() -> None:
    reply = _reply(in_scope=False, redirect_to="huginn", answer="It reproduces fine.", evidence=[])

    message = render_agent_reply(LOKI_SCOPE, reply)

    assert "@huginn" in message
    assert "It reproduces fine" not in message


def test_an_off_topic_refusal_renders_the_one_line_refusal() -> None:
    reply = _reply(in_scope=False, answer="Sunny, 21 degrees.", evidence=[])

    message = render_agent_reply(LOKI_SCOPE, reply)

    assert message == f"Loki: {LOKI_SCOPE.refusal_line()}"


def test_an_answer_the_tone_filter_emptied_renders_the_failure_line() -> None:
    reply = _reply().filtered(lambda _text: "")

    assert render_agent_reply(HUGINN_SCOPE, reply) == f"Huginn: {FAILED_REPLY}"


def test_broadcast_mentions_in_an_answer_are_neutralised() -> None:
    reply = _reply(answer="Heads up @channel and @here, see @all.")

    message = render_agent_reply(HUGINN_SCOPE, reply)

    assert "@channel" not in message
    assert "@here" not in message
    assert "@all" not in message


def test_an_checks_rerun_reply_needs_no_evidence() -> None:
    reply = _reply(
        answer="Intake is running again on the current head.",
        evidence=[],
        action_taken="checks_rerun",
    )

    assert reply.action_taken == "checks_rerun"


def test_an_unsummarised_checks_rerun_is_still_named_in_the_room() -> None:
    line = render_unsummarised_actions(HUGINN_SCOPE, ["checks_rerun"])

    assert "re-ran the checks" in line
