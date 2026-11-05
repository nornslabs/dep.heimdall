"""Skill loading, startup validation and the local ``load_skill`` tool."""

from pathlib import Path

import pytest
from pydantic import BaseModel
from structlog.testing import capture_logs

from agents._guardrails import wrap_pr_data
from agents._runtime import (
    AgentBudget,
    AgentRun,
    Completed,
    SubmitTool,
    ToolBox,
    run_agent,
)
from agents._skills import SkillSet, load_skills
from heimdall.adapters.ai import ChatMessage, ToolCall
from heimdall.shared.agents import AgentName
from heimdall.shared.errors import ConfigInvalid
from tests.unit.agents.scripted_ai import ScriptedAi, tool_reply

ALLOWED = frozenset({"start_repro", "get_repro"})


def _text(
    name: str,
    *,
    description: str = "A short description.",
    when: str = "Before the task.",
    tools: str = "[]",
    body: str = "Do the thing.",
) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"when: {when}\n"
        f"tools: {tools}\n"
        "---\n\n"
        f"{body}\n"
    )


def _write(root: Path, agent: AgentName, filename: str, text: str) -> Path:
    path = root / agent.value / "skills" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _load(
    root: Path,
    *,
    agent: AgentName = AgentName.HUGINN,
    allowed: frozenset[str] = ALLOWED,
    required: frozenset[str] = frozenset(),
) -> SkillSet:
    return load_skills(agent, allowed, required, agents_root=root)


def test_loads_a_valid_skill_and_returns_its_body(tmp_path: Path) -> None:
    body = "# Steps\n\n1. Check the command.\n2. Start one paired attempt."
    _write(
        tmp_path, AgentName.HUGINN, "pick_test_command.md", _text("pick_test_command", body=body)
    )

    skills = _load(tmp_path, required=frozenset({"pick_test_command"}))

    assert skills.names() == ("pick_test_command",)
    assert skills.load("pick_test_command") == body


def test_frontmatter_is_not_part_of_the_body(tmp_path: Path) -> None:
    _write(tmp_path, AgentName.HUGINN, "pick_test_command.md", _text("pick_test_command"))

    skills = _load(tmp_path)

    assert skills.load("pick_test_command") == "Do the thing."


def test_crlf_frontmatter_is_accepted(tmp_path: Path) -> None:
    text = _text("pick_test_command").replace("\n", "\r\n")
    _write(tmp_path, AgentName.HUGINN, "pick_test_command.md", text)

    assert _load(tmp_path).load("pick_test_command") == "Do the thing."


def test_prompt_index_lists_sorted_names_and_descriptions_only(tmp_path: Path) -> None:
    _write(
        tmp_path,
        AgentName.LOKI,
        "beta.md",
        _text(
            "beta", description="Second description.", when="Hidden beta trigger.", body="B body"
        ),
    )
    _write(
        tmp_path,
        AgentName.LOKI,
        "alpha.md",
        _text(
            "alpha", description="First description.", when="Hidden alpha trigger.", body="A body"
        ),
    )

    index = _load(tmp_path, agent=AgentName.LOKI).prompt_index()

    assert index == "## Skills\n\n- alpha: First description.\n- beta: Second description."
    assert "Hidden" not in index
    assert "body" not in index


async def test_unknown_skill_is_an_error_without_the_raw_request(tmp_path: Path) -> None:
    _write(tmp_path, AgentName.HUGINN, "alpha.md", _text("alpha"))
    tool = _load(tmp_path).tool()

    result = await tool.handler({"name": "alpha; rm -rf /"})

    assert isinstance(result, str)
    assert result.startswith("error: unknown skill")
    assert ";" not in result
    assert "/" not in result


def test_tool_spec_offers_every_discovered_name(tmp_path: Path) -> None:
    _write(tmp_path, AgentName.HUGINN, "beta.md", _text("beta"))
    _write(tmp_path, AgentName.HUGINN, "alpha.md", _text("alpha"))

    spec = _load(tmp_path).tool().spec

    assert spec.name == "load_skill"
    assert spec.input_schema == {
        "type": "object",
        "properties": {"name": {"type": "string", "enum": ["alpha", "beta"]}},
        "required": ["name"],
        "additionalProperties": False,
    }


async def test_load_skill_returns_the_body_and_logs_counts_only(tmp_path: Path) -> None:
    body = "Check the saved counts before deciding."
    _write(tmp_path, AgentName.HUGINN, "judge_repro.md", _text("judge_repro", body=body))
    tool = _load(tmp_path).tool()

    with capture_logs() as logs:
        result = await tool.handler({"name": "judge_repro"})

    assert result == body
    assert logs[-1]["event"] == "agent_skill_loaded"
    assert logs[-1]["skill"] == "judge_repro"
    assert logs[-1]["characters"] == len(body)
    assert body not in str(logs)


async def test_toolbox_runs_load_skill(tmp_path: Path) -> None:
    _write(tmp_path, AgentName.HUGINN, "alpha.md", _text("alpha", body="Body text."))
    toolbox = ToolBox(frozenset({"load_skill"}), (_load(tmp_path).tool(),))

    result = await toolbox.call("load_skill", {"name": "alpha"})

    assert (result.text, result.ran) == ("Body text.", True)


async def test_toolbox_rejects_an_unknown_skill_and_lists_valid_names(tmp_path: Path) -> None:
    _write(tmp_path, AgentName.HUGINN, "alpha.md", _text("alpha"))
    toolbox = ToolBox(frozenset({"load_skill"}), (_load(tmp_path).tool(),))

    result = await toolbox.call("load_skill", {"name": "missing"})

    assert result.ran is False
    assert "is not one of" in result.text
    assert "alpha" in result.text


INVALID_CASES = [
    ("missing-open.md", "name: missing_open\n---\nbody", "missing its opening"),
    ("missing-close.md", "---\nname: missing_close\n", "missing its closing"),
    ("bad-yaml.md", "---\nname: [\n---\nbody", "not valid YAML"),
    ("not-mapping.md", "---\n- alpha\n---\nbody", "must be a YAML mapping"),
    (
        "missing_description.md",
        "---\nname: missing_description\nwhen: Later.\ntools: []\n---\nbody",
        "missing required field 'description'",
    ),
    (
        "missing_tools.md",
        "---\nname: missing_tools\ndescription: D\nwhen: W\n---\nbody",
        "missing required field 'tools'",
    ),
    (
        "unknown_field.md",
        "---\nname: unknown_field\ndescription: D\nwhen: W\ntools: []\ntool: []\n---\nbody",
        "unknown field 'tool'",
    ),
    (
        "blank_description.md",
        "---\nname: blank_description\ndescription: '   '\nwhen: W\ntools: []\n---\nbody",
        "'description' must not be empty",
    ),
    (
        "multiline_description.md",
        "---\nname: multiline_description\ndescription: |\n"
        "  one\n  two\nwhen: W\ntools: []\n---\nbody",
        "'description' must be a single line",
    ),
    (
        "long_description.md",
        "---\nname: long_description\ndescription: "
        + "a" * 161
        + "\nwhen: W\ntools: []\n---\nbody",
        "at most 160 characters",
    ),
    (
        "BadName.md",
        "---\nname: BadName\ndescription: D\nwhen: W\ntools: []\n---\nbody",
        "must match ^[a-z][a-z0-9_]*$",
    ),
    (
        "mismatch.md",
        "---\nname: other_name\ndescription: D\nwhen: W\ntools: []\n---\nbody",
        "must equal the filename stem",
    ),
    (
        "tools_not_list.md",
        "---\nname: tools_not_list\ndescription: D\nwhen: W\ntools: start_repro\n---\nbody",
        "'tools' must be a list",
    ),
    (
        "tools_not_strings.md",
        "---\nname: tools_not_strings\ndescription: D\nwhen: W\ntools: [1]\n---\nbody",
        "only strings",
    ),
    (
        "duplicate_tools.md",
        "---\nname: duplicate_tools\ndescription: D\nwhen: W\n"
        "tools: [start_repro, start_repro]\n---\nbody",
        "duplicate names",
    ),
    (
        "disallowed_tool.md",
        "---\nname: disallowed_tool\ndescription: D\nwhen: W\ntools: [get_diff]\n---\nbody",
        "outside the allow-list",
    ),
    (
        "load_skill_tool.md",
        "---\nname: load_skill_tool\ndescription: D\nwhen: W\ntools: [load_skill]\n---\nbody",
        "outside the allow-list",
    ),
    (
        "empty_body.md",
        "---\nname: empty_body\ndescription: D\nwhen: W\ntools: []\n---\n\n   \n",
        "body must not be empty",
    ),
    (
        "long_body.md",
        "---\nname: long_body\ndescription: D\nwhen: W\ntools: []\n---\n\n" + "x" * 8_001,
        "at most 8000 characters",
    ),
]


@pytest.mark.parametrize(("filename", "text", "expected"), INVALID_CASES)
def test_invalid_skill_fails_startup(
    tmp_path: Path, filename: str, text: str, expected: str
) -> None:
    _write(tmp_path, AgentName.HUGINN, filename, text)

    with pytest.raises(ConfigInvalid) as raised:
        _load(tmp_path)

    message = str(raised.value)
    assert filename in message
    assert expected in message


def test_missing_directory_fails_startup(tmp_path: Path) -> None:
    with pytest.raises(ConfigInvalid, match="skills directory is missing"):
        _load(tmp_path)


def test_empty_directory_fails_startup(tmp_path: Path) -> None:
    (tmp_path / AgentName.HUGINN.value / "skills").mkdir(parents=True)

    with pytest.raises(ConfigInvalid, match="no skills found"):
        _load(tmp_path)


def test_missing_required_skill_fails_startup(tmp_path: Path) -> None:
    _write(tmp_path, AgentName.HUGINN, "alpha.md", _text("alpha"))

    with pytest.raises(ConfigInvalid, match="missing required skill 'beta'"):
        _load(tmp_path, required=frozenset({"alpha", "beta"}))


def test_non_markdown_and_nested_files_are_ignored(tmp_path: Path) -> None:
    _write(tmp_path, AgentName.HUGINN, "alpha.md", _text("alpha"))
    _write(tmp_path, AgentName.HUGINN, "notes.txt", "not a skill")
    _write(tmp_path, AgentName.HUGINN, "nested/bad.md", "not a skill")

    assert _load(tmp_path).names() == ("alpha",)


class Verdict(BaseModel):
    result: str


async def _read_head() -> str:
    return "head-1"


async def test_runtime_loads_a_skill_then_submits(tmp_path: Path) -> None:
    body = "Use the saved paired counts."
    _write(tmp_path, AgentName.HUGINN, "judge_repro.md", _text("judge_repro", body=body))
    skills = _load(tmp_path)
    ai = ScriptedAi(
        [
            tool_reply(ToolCall("c1", "load_skill", {"name": "judge_repro"})),
            tool_reply(ToolCall("c2", "submit_verdict", {"result": "ok"})),
        ]
    )
    run = AgentRun(
        agent="huginn",
        ai=ai,
        toolbox=ToolBox(frozenset({"load_skill"}), (skills.tool(),)),
        submit=SubmitTool("submit_verdict", Verdict, "Finish with the verdict."),
        budget=AgentBudget(context_tokens=10_000, run_tokens=100_000, max_turns=4),
        head_sha="head-1",
        read_current_head=_read_head,
    )

    outcome = await run_agent(run, [ChatMessage("user", "judge the run")])

    assert isinstance(outcome, Completed)
    # Every tool result the runtime hands back is wrapped as PR data, skill playbooks included.
    expected = ChatMessage("tool", wrap_pr_data("load_skill", body), tool_call_id="c1")
    assert ai.requests[1][0][-1] == expected
