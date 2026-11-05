"""Heimdall's intake-failure reply: LLM wording, deterministic greeting, safe fallback."""

from agents.heimdall._intake_reply import draft_intake_reply
from heimdall.modules.intake.api import RuleName, RuleResult
from heimdall.shared.errors import AiUnavailable
from tests.unit.agents.scripted_ai import ScriptedAi, text_reply

FAILED = (
    RuleResult(RuleName.LINKED_ISSUE, passed=False, detail="Add `Closes #n` to the body."),
    RuleResult(RuleName.REQUIRED_LABELS, passed=False, detail="Add required labels: hackathon."),
)


async def test_reply_greets_the_author_and_keeps_the_model_points() -> None:
    ai = ScriptedAi([text_reply("- Link an issue with `Closes #n`.\n- Add the hackathon label.")])

    body = await draft_intake_reply(ai, "octocat", 24, FAILED)

    assert body.startswith("Hey @octocat,")
    assert "- Link an issue with `Closes #n`." in body


async def test_model_greeting_is_not_trusted_for_the_mention() -> None:
    ai = ScriptedAi([text_reply("Hey @everyone, please fix:\n- Link an issue.")])

    body = await draft_intake_reply(ai, "octocat", 24, FAILED)

    assert body.startswith("Hey @octocat,")
    assert "@everyone" not in body


async def test_reply_falls_back_to_rule_details_when_the_model_is_unavailable() -> None:
    ai = ScriptedAi([AiUnavailable("down")])

    body = await draft_intake_reply(ai, "octocat", 24, FAILED)

    assert body.startswith("Hey @octocat,")
    assert "- Add `Closes #n` to the body." in body
    assert "- Add required labels: hackathon." in body


async def test_reply_falls_back_when_the_model_leaks_a_secret() -> None:
    leaked = "- token ghp_" + "a" * 36
    ai = ScriptedAi([text_reply(leaked)])

    body = await draft_intake_reply(ai, "octocat", 24, FAILED)

    assert "ghp_" not in body
    assert "- Add required labels: hackathon." in body


async def test_failed_rule_details_reach_the_model_as_marked_data() -> None:
    ai = ScriptedAi([text_reply("- Do the thing.")])

    await draft_intake_reply(ai, "octocat", 24, FAILED)

    prompt = " ".join(message.content for message in ai.requests[0][0])
    assert "<pr_data" in prompt
    assert "Add required labels: hackathon." in prompt
