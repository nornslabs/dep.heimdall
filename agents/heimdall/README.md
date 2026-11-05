# Heimdall

Heimdall assembles a current-head review card and approval-bound draft from stored findings and
reproduction results. Valid slash commands are parsed as data and dispatched to trusted handlers.

## Skills

| Skill | When | Allowed tools | Outcome |
|---|---|---|---|
| `write_card` | The pull request reaches `pr.ready_for_card` and both checks are complete for the head. | `list_findings`, `get_repro`, `save_card` | Build one current-head card from stored findings and the latest repro result. |
| `draft_change_request` | A maintainer asks Heimdall to request changes on the pull request. | `list_findings`, `get_repro`, `save_draft` | Draft a specific, polite change request tied to cited findings. |
| `draft_thanks` | A maintainer asks Heimdall to thank the pull request author. | `save_draft` | Draft a short thank-you that states what was verified. |
| `draft_close` | A maintainer asks Heimdall to close the pull request. | `save_draft` | Draft a close message that gives the reason and cites the evidence. |
| `explain_why` | A maintainer asks Heimdall why the pull request received its current result. | `list_findings`, `get_repro` | Explain the current card's verdict and gaps from saved data. |
| `mechanical_task` | A saved finding is lint, typo, rename, `missing_import`, docstring or changelog. | `create_mechanical_task`, `list_findings` | Create one policy-gated task; never post directly. |

All six skills are required (`REQUIRED_SKILLS` in `config.py`).

| File | Role |
|---|---|
| `agent.py` | Card assembly and command routing |
| `runner.py` | Consumer of the `heimdall` queue only: runs `heimdall.build_card`, then queues the approval message for the repo's room, and holds the agent's Switch connection beside the worker (`../_presence_runner.py`) so it answers a maintainer's `ping`/`hello`, and tells anyone else to ask an admin to add them to `maintainers` in `heimdall.yml` |
| `prompt.md` | Trust boundaries, allowed tools, and output format |
| `allowed_tools.py` | Heimdall's scoped MCP tool allowlist |
| `schemas.py` | `DeskResult` submit schema and `SUBMIT_RESULT`, plus `desk_result_problems` (the id must be work this run saved) |
| `_message.py` | `render_desk_result`: the room message built from a validated result |
| `config.py` | `AGENT_NAME`, `MAX_TURNS = 8`, `BUDGET` (24k context, 100k run tokens, no diff or file reads, 2 drafts), `REQUIRED_SKILLS`, `load_ai_config()` (`LLM_HEIMDALL_*`) |
| `skills/` | Six required playbooks (see table above) |

The card and command flows are deterministic today. The worker builds Heimdall's `AiAdapter` from
its own config at startup, so a bad model setting fails before any job runs.

`build_review_card` returns `approval_message`, which adds hidden draft identity and hash metadata
for the bridge. The marker is outside the saved draft body, so the approval hash still covers
only the exact text Gjallarhorn may post. Heimdall's MCP scope includes `create_mechanical_task`,
which saves a policy-gated task draft and returns its `heimdall-task` block.
