# Heimdall

Heimdall runs on the shared runtime (`agents/_runtime.py`): the model decides what to do next;
its ToolBox, budgets, guardrails and `submit_result` decide what it may do. It assembles a
current-head review card or drafts answers to maintainer commands using stored triage findings and
reproduction results.

## Conversations

When mentioned, Heimdall answers about the card, intake rules, drafts and next steps only
(`scope.py`, `conversation.md`). Ask mode uses `ASK_TOOLS`; a maintainer's request to act opens
`save_draft` only, with the usual `DeskGuard` (`mention.py`); a mention never opens `save_card`
or `create_mechanical_task`. A saved draft still waits for the 👍. See
`../README.md#conversations-answering-agentmention`.

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
| `agent.py` | `run_heimdall`: thin entry point running Heimdall on `_runtime.py` |
| `runner.py` | Consumer of the `heimdall` queue: runs `heimdall.build_card` and `heimdall.run_command`, queues room posts, and holds its Switch connection so it queues an `agent.mention` for each message a maintainer addresses to it (its `parse_command` verbs excepted) |
| `context.py` | `build_desk_brief` and `build_messages`: pre-fetches repro and finding data into a `pr_data` brief |
| `guardrails.py` | `CommandGuard` (maintainers only), `DeskGuard` (at most 2 drafts, valid finding citations only), `SavedIdTracker`, and `submit_desk_result_tool` |
| `prompt.md` | Role prompt, rules, and output format |
| `allowed_tools.py` | Heimdall's scoped MCP tool allowlist |
| `schemas.py` | `DeskResult` submit schema and `SUBMIT_RESULT`, plus `desk_result_problems` |
| `_message.py` | `render_desk_result`: room message built from a validated result |
| `config.py` | `AGENT_NAME`, `MAX_TURNS = 8`, `BUDGET` (24k context, 100k run tokens, no diff or file reads, 2 drafts), `REQUIRED_SKILLS`, `load_ai_config()` |
| `skills/` | Six required playbooks (see table above) |

The server renders card text and hashes drafts. Posting to GitHub still needs maintainer approval
with a matching hash.
