# PRD: Real-Time Agent Feedback in Switch

## Problem

When a maintainer mentions an agent (`@heimdall`, `@huginn`, `@loki`), there is **zero feedback** for 10–60 seconds while the agent thinks, calls tools, and talks to GitHub. The maintainer sees nothing — no acknowledgment, no progress, no signal the agent is alive. This causes:

- Duplicate mentions (user thinks it didn't work)
- Confusion: "is it down or just slow?"
- No parity with Slack/Discord/Mattermost native UX

---

## Solution: Four-Layer Feedback System

| Layer | Trigger | UX | Effort |
|-------|---------|-----|--------|
| **1. Reaction ACK** | Mention received | 👀 on the mention message | 2 hrs |
| **2. Typing Indicator** | Job starts processing | "Heimdall is typing…" in channel | 1 day |
| **3. Ephemeral Progress Post** | Tool calls / stages | Single updatable message: `🔄 Fetching PR…` → `🔄 Running tests…` → `✅ Done` | 1 day |
| **4. Rich Final Reply** | Job completes | Structured card with buttons, sections, unfurled links | 1 day |

**Bonus**: Slash commands (`/card`, `/repro`, `/status`) for discoverability — 0.5 day.

---

## User Experience

### Before
```
Maintainer: @heimdall status #42
... 30s silence ...
Heimdall: **PR #42 — Fix Confirmed**
          Huginn: fix_confirmed
          Loki: 2 findings
          [Approve] [Request Changes]
```

### After
```
Maintainer: @heimdall status #42
[Heimdall reacts 👀]                    ← instant ACK
[Heimdall is typing…]                   ← typing indicator
Heimdall: 🔄 Fetching PR #42…           ← ephemeral post (updates in place)
Heimdall: 🔄 Running Loki review…       
Heimdall: 🔄 Huginn reproduction…       
Heimdall: ✅ **PR #42 — Fix Confirmed** ← final rich card (replaces ephemeral)
          Huginn: fix_confirmed ✓
          Loki: 2 findings 🔍
          [Approve] [Request Changes] [View Diff] [Re-run Tests]
[Heimdall reacts ✅]                    ← completion ACK
```

---

## Technical Design

### 1. Reaction ACK — `agents/_mention_task.py`

```python
# On job pickup, before any processing:
await poster.add_reaction(message_id, "eyes")

try:
    # ... processing ...
    await poster.remove_reaction(message_id, "eyes")
    await poster.add_reaction(message_id, "white_check_mark")
except Exception:
    await poster.remove_reaction(message_id, "eyes")
    await poster.add_reaction(message_id, "x")
    raise
```

**Requires**: `AgentPoster.add_reaction(post_id, emoji)` — thin wrapper over existing `SwitchSender.react()`.

---

### 2. Typing Indicator — `src/heimdall/adapters/switch/agent/_post.py`

```python
# New config fields in AgentConnectionConfig:
mattermost_bot_token: str | None = None
mattermost_team_id: str | None = None

# New method on AgentPoster:
async def send_typing(self, channel_id: str) -> None:
    if not (self._config.mattermost_bot_token and self._config.mattermost_team_id):
        return
    url = f"{self._config.base_url}/api/v4/users/me/teams/{team_id}/channels/{channel_id}/typing"
    await self._client.post(url)  # fire-and-forget, 200 OK = typing shows for ~5s
```

**Refresh loop** in mention task (every 4s while processing):
```python
async def _with_typing(poster, channel_id, coro):
    stop = asyncio.Event()
    async def refresh():
        while not stop.is_set():
            await poster.send_typing(channel_id)
            try: await asyncio.wait_for(stop.wait(), 4.0)
            except TimeoutError: pass
    task = asyncio.create_task(refresh())
    try: return await coro()
    finally: stop.set(); await task
```

**Env vars** (per agent):
```
SWITCH_HEIMDALL_MM_BOT_TOKEN=...
SWITCH_HEIMDALL_MM_TEAM_ID=...
```
*No-op if missing — graceful degradation.*

---

### 3. Ephemeral Progress Post — `AgentPoster.post_progress()`

```python
async def post_progress(self, channel_id: str, thread_id: str | None, text: str, *, update_post_id: str | None = None) -> str:
    """Post or update a progress message. Returns the post_id for next update."""
    if update_post_id:
        # PATCH /api/v4/posts/{post_id}
        await self._client.patch(f"{POSTS_PATH}/{update_post_id}", json={"message": text})
        return update_post_id
    else:
        # POST /api/v4/posts with root_id=thread_id
        return await self._rest.create_post(channel_id, text, root_id=thread_id or "")
```

**Usage in agent tools** (e.g., `start_repro`, `save_findings`, `get_pull_request`):
```python
# In tool handler, before long operation:
progress_id = await poster.post_progress(channel, thread, "🔄 Fetching PR…")
result = await github.get_pull_request(...)
await poster.post_progress(channel, thread, "🔄 Running Loki review…", update_post_id=progress_id)
# ...
await poster.post_progress(channel, thread, "✅ Done", update_post_id=progress_id)
```

**Final reply replaces the progress post** (same `post_id`, new content) — no duplicate messages.

---

### 4. Rich Final Reply — Structured Attachments

Replace plain markdown with Mattermost attachments:

```python
def render_desk_result_rich(result: DeskResult, ...) -> dict:
    return {
        "channel_id": room_id,
        "root_id": thread_id,
        "props": {
            "attachments": [{
                "title": f"PR #{number} — {status_emoji} {result.action.title()}",
                "color": color_for_action(result.action),
                "fields": [
                    {"title": "Huginn", "value": huginn_summary, "short": True},
                    {"title": "Loki", "value": loki_summary, "short": True},
                ],
                "actions": [
                    {"name": "Approve", "integration": {"url": f"{WEBHOOK_URL}/approve/{draft_id}"}},
                    {"name": "Request Changes", "integration": {"url": f"{WEBHOOK_URL}/reject/{draft_id}"}},
                    {"name": "View Diff", "integration": {"url": f"{GITHUB_URL}/files"}},
                    {"name": "Re-run Tests", "integration": {"url": f"{WEBHOOK_URL}/rerun/{pr_id}"}},
                ],
            }]
        }
    }
```

**Button callbacks** → bridge webhook → new mention job (`heimdall.run_command` with structured args).

---

### 5. Slash Commands — `apps/bridge/_commands.py`

```python
# New endpoint: POST /switch/command
# Mattermost sends: {command: "/heimdall", text: "card #42", channel_id, user_id, ...}

COMMANDS = {
    "card":   ("Build approval card",   lambda args: queue_job("heimdall.build_card", pr=args.pr)),
    "repro":  ("Trigger reproduction",  lambda args: queue_job("huginn.run_repro", pr=args.pr, test_cmd=args.cmd)),
    "review": ("Request Loki review",   lambda args: queue_job("loki.run_review", pr=args.pr)),
    "status": ("Show PR status",        lambda args: post_status_card(args.pr)),
    "config": ("Show repo config",      lambda args: post_config_card(args.pr)),
}
```

**Registration**: Add to `apps.bridge.setup` — creates slash commands in Mattermost via API.

---

## Implementation Order

| Phase | Features | Files | Est. |
|-------|----------|-------|------|
| **1** | Reaction ACK + Typing Indicator | `_mention_task.py`, `_post.py`, `config.py`, `ports.py`, 3 runners | 1.5 days |
| **2** | Ephemeral Progress Posts | `_post.py`, tool handlers (repro, review, card) | 1 day |
| **3** | Rich Replies (attachments + buttons) | `_message.py` (renderers), bridge webhook for actions | 1 day |
| **4** | Slash Commands | `apps/bridge/_commands.py`, setup registration | 0.5 day |

**Total**: ~4 days for full feature set.

---

## Configuration

### New Env Vars (per agent)
```bash
# Required for typing indicator only
SWITCH_HEIMDALL_MM_BOT_TOKEN=mm_bot_token
SWITCH_HEIMDALL_MM_TEAM_ID=team_id
SWITCH_HUGGIN_MM_BOT_TOKEN=...
SWITCH_HUGGIN_MM_TEAM_ID=...
SWITCH_LOKI_MM_BOT_TOKEN=...
SWITCH_LOKI_MM_TEAM_ID=...

# Optional: webhook base for button callbacks (shared)
HEIMDALL_BRIDGE_WEBHOOK_URL=https://your-host/switch/command
```

### Fallback Behavior
| Feature | If Not Configured |
|---------|-------------------|
| Typing indicator | Silent no-op (no error) |
| Progress posts | Falls back to final reply only |
| Rich formatting | Falls back to markdown |
| Slash commands | Not registered (mentions still work) |

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| REST typing API doesn't support threads | Typing shows in main channel; progress posts use `root_id` for correct threading |
| Bot token rotation | Document in ops guide; use dedicated bot per agent |
| Progress post updates race with final reply | Same `post_id` — final update wins; idempotent |
| Button callbacks need auth | Bridge verifies request signature; maps to maintainer via Switch user_id |
| Extra HTTP calls add latency | Fire-and-forget for typing; progress posts async; <50ms overhead |

---

## Success Metrics

1. **Reaction ACK** appears <500ms after mention (99th percentile)
2. **Typing indicator** visible within 1s of job start, refreshed until reply
3. **Progress posts** update every 5–15s during long operations
4. **Final reply** replaces progress post (no duplicate messages)
5. **Zero errors** in logs from feedback calls
6. **No regression** in agent response latency

---

## Out of Scope (Explicitly Not Doing)

- WebSocket typing with thread support (v2)
- Agent-to-agent mentions (`@heimdall ask @loki…`)
- Voice messages / transcription
- Scheduled reminders ("ping me when checks pass")
- Custom emoji / animated status
- Private threads for secrets
- Agent personality modes
- Analytics dashboard in Switch
- Cross-repo room awareness

---

## Dependencies

- Mattermost bot account per agent with `post:all`, `channel:read`, `reaction:write` permissions
- Network: agent workers → Mattermost REST API (same as existing `SwitchSender`)
- No Switch server changes required

---

## Rollout

1. Deploy env vars to staging (bot tokens, team ID)
2. Enable Phase 1 (reactions + typing) for Heimdall only
3. Verify in Switch: mention → 👀 → typing → reply → ✅
4. Enable for Huginn, Loki
5. Phase 2: add progress posts to tool handlers
6. Phase 3: rich attachments + buttons
7. Phase 4: slash commands
8. Document in ops guide