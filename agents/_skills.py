"""Load each agent's markdown playbooks and expose them through a local ``load_skill`` tool.

The system prompt gets names and one-line descriptions only. The model asks for one playbook
when its task matches, and the playbook text comes back as a tool result. Validation happens
at startup, so a broken skill fails the process instead of a live PR run.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agents._skill_frontmatter import Skill, read_skill
from agents._toolbox import AgentTool
from heimdall.adapters.ai import ToolSpec
from heimdall.shared.agents import AgentName
from heimdall.shared.errors import ConfigInvalid
from heimdall.shared.logging import get_logger

LOAD_SKILL_TOOL = "load_skill"
SKILLS_DIRECTORY = "skills"
AGENTS_ROOT = Path(__file__).resolve().parent
MAX_VALIDATION_ERRORS = 20
MAX_SKILL_NAME_LENGTH = 64
_NOT_SAFE_NAME_CHARACTERS = re.compile(r"[^a-z0-9_]")

log = get_logger(__name__)


@dataclass(frozen=True)
class SkillSet:
    """One agent's validated playbooks, plus the prompt index and ``load_skill`` tool.

    Attributes:
        agent: The agent these skills belong to.
        skills: Discovered playbooks, sorted by name.
    """

    agent: AgentName
    skills: tuple[Skill, ...]

    def names(self) -> tuple[str, ...]:
        """Return every discovered skill name, sorted."""
        return tuple(skill.name for skill in self.skills)

    def prompt_index(self) -> str:
        """Render the compact skill list added to the agent's system prompt.

        Returns:
            A ``## Skills`` section with one ``name: description`` line per skill, or an
            empty string when the agent has no skills.
        """
        if not self.skills:
            return ""
        lines = ["## Skills", ""]
        lines.extend(f"- {skill.name}: {skill.description}" for skill in self.skills)
        return "\n".join(lines)

    def load(self, name: str) -> str | None:
        """Return one playbook's body, or None when the name is not a discovered skill."""
        for skill in self.skills:
            if skill.name == name:
                return skill.body
        return None

    def tool(self) -> AgentTool:
        """Build the local ``load_skill`` tool for the agent's ToolBox."""
        spec = ToolSpec(
            LOAD_SKILL_TOOL,
            "Load one skill's full playbook by name. Returns the playbook text.",
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": list(self.names())},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        )
        return AgentTool(spec, self._load)

    async def _load(self, arguments: Mapping[str, object]) -> object:
        """Return the requested body, or an error that never copies the request verbatim."""
        requested = arguments.get("name")
        name = requested if isinstance(requested, str) else ""
        body = self.load(name)
        if body is None:
            return f"error: unknown skill {_safe_name(name)!r}"
        log.info(
            "agent_skill_loaded",
            agent=self.agent.value,
            skill=name,
            characters=len(body),
        )
        return body


def load_skills(
    agent: AgentName,
    allowed_tools: frozenset[str],
    required: frozenset[str],
    *,
    agents_root: Path = AGENTS_ROOT,
) -> SkillSet:
    """Read and validate every skill file for one agent.

    Args:
        agent: The agent whose ``skills/`` directory to read.
        allowed_tools: The agent's MCP allow-list; a skill may name only these tools.
        required: Skill names that must exist; a missing one fails startup.
        agents_root: Directory holding the per-agent folders; tests pass a temporary root.

    Returns:
        The validated skills, sorted by name.

    Raises:
        ConfigInvalid: a file cannot be read, frontmatter or body is invalid, a name is
            duplicated, or a required skill is missing.
    """
    directory = agents_root / agent.value / SKILLS_DIRECTORY
    skills, errors = _discover(directory, allowed_tools)
    errors.extend(_missing_errors(skills, required))
    if not skills and not errors:
        errors.append(f"{directory}: no skills found")
    if errors:
        log.error("agent_skills_invalid", agent=agent.value, errors=len(errors))
        raise ConfigInvalid(_invalid_message(agent, errors))
    ordered = tuple(sorted(skills, key=lambda skill: skill.name))
    log.info(
        "agent_skills_loaded",
        agent=agent.value,
        count=len(ordered),
        skills=[skill.name for skill in ordered],
    )
    return SkillSet(agent, ordered)


def _discover(directory: Path, allowed_tools: frozenset[str]) -> tuple[list[Skill], list[str]]:
    """Read every direct ``*.md`` child, collecting valid skills and validation errors."""
    if not directory.is_dir():
        return [], [f"{directory}: skills directory is missing"]
    paths = sorted(directory.glob("*.md"))
    if not paths:
        return [], [f"{directory}: no skills found"]
    skills: list[Skill] = []
    errors: list[str] = []
    for path in paths:
        skill, problems = read_skill(path, allowed_tools)
        if skill is None:
            errors.extend(problems)
        else:
            skills.append(skill)
    return skills, errors


def _missing_errors(skills: list[Skill], required: frozenset[str]) -> list[str]:
    """Name every required skill that no file provides."""
    found = {skill.name for skill in skills}
    return [f"missing required skill {name!r}" for name in sorted(required - found)]


def _invalid_message(agent: AgentName, errors: list[str]) -> str:
    """Build the startup failure text, capped so a bad directory cannot flood the log."""
    shown = errors[:MAX_VALIDATION_ERRORS]
    lines = [f"invalid skills for {agent.value}:"]
    lines.extend(f"- {error}" for error in shown)
    remaining = len(errors) - len(shown)
    if remaining:
        lines.append(f"- ... and {remaining} more")
    return "\n".join(lines)


def _safe_name(name: str) -> str:
    """Reduce an unknown request to the characters a skill name may contain."""
    return _NOT_SAFE_NAME_CHARACTERS.sub("", name.casefold())[:MAX_SKILL_NAME_LENGTH]
