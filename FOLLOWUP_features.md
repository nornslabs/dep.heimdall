# Follow-up Features: Beyond Typing Indicators

After implementing typing indicators, here are high-value enhancements for the agent ↔ Switch integration:

---

## 1. **Thread-Aware Typing (WebSocket)**
**Current limitation**: REST typing API doesn't support `parent_id` (threads).
- **Upgrade**: Use Mattermost WebSocket `user_typing` action with `parent_id`
- **Benefit**: Typing appears in the correct thread, not main channel
- **Effort**: Moderate — need persistent WS connection per agent (already have one for mentions)

---

## 2. **Reaction Acknowledgments**
Instead of (or alongside) typing, react to the mention immediately:
- 👀 "I see you" — when job is queued
- ⚙️ "Working" — when job starts processing  
- ✅/❌ — when reply posts / fails

```python
# In agent_mention_task, after queueing but before processing:
await poster.add_reaction(message_id, "eyes")  # or custom emoji
```

**Benefit**: Works in threads, visible immediately, persists as audit trail.

---

## 3. **Progress Updates for Long Jobs**
For multi-step operations (repro runs, card builds), post incremental status:
```
🔄 Heimdall: Starting reproduction for #42…
🔄 Heimdall: Tests running (2/5 suites)…
🔄 Heimdall: Analyzing results…
✅ Heimdall: Fix confirmed — tests fail without, pass with change.
```

**Implementation**: 
- Add `post_progress(channel_id, thread_id, message)` to `AgentPoster`
- Call from tool handlers (e.g., `start_repro` tool posts "Tests starting…")
- Use `root_id` to thread under the original mention

---

## 4. **Slash Commands / Interactive Menus**
Mattermost supports slash commands and dialog/interactive menus:
- `/heimdall status #42` → structured card reply
- `/heimdall repro #42 --pytest` → trigger repro with options
- `/heimdall approve <draft_id>` → approve without emoji hunting

**Benefit**: Discoverable, scriptable, works on mobile.

---

## 5. **Rich Message Formatting (Blocks/Attachments)**
Mattermost supports message attachments and blocks:
- **Cards**: Structured sections (Summary, Findings, Actions) with colors
- **Code blocks**: Syntax-highlighted diffs, test output
- **Buttons**: "Approve", "Reject", "View on GitHub" actions

```python
# Instead of plain markdown:
payload = {
    "channel_id": room_id,
    "root_id": thread_id,
    "props": {
        "attachments": [{
            "title": "PR #42 — Fix Confirmed",
            "color": "#2eb886",
            "fields": [...],
            "actions": [
                {"name": "Approve", "integration": {"url": "..."}},
                {"name": "View on GitHub", "integration": {"url": "..."}}
            ]
        }]
    }
}
```

---

## 6. **Mention Completion Notifications (DM/Email)**
If a maintainer mentions an agent then leaves the room:
- DM them when the reply is ready
- Or email if DM fails
- Configurable per-user preference

---

## 7. **Agent Status Command**
`@heimdall status` → shows:
- Current queue depth
- Active jobs (PR #, stage)
- Model latency / token usage
- Last 5 completions

---

## 8. **Thread History / Context Recall**
When maintainer replies in a thread:
- Agent sees full thread context (not just the new message)
- Can reference prior answers: "As I mentioned earlier…"

---

## 9. **Typing + Progress Hybrid**
Combine both for best UX:
```
[Heimdall is typing…]           ← typing indicator
Heimdall: 🔄 Fetching PR #42…   ← progress post (ephemeral/update)
Heimdall: 🔄 Running tests…     
Heimdall: ✅ Fix confirmed       ← final answer (replaces progress)
```

---

## 10. **Multi-Agent Coordination Signals**
When multiple agents work on one PR:
- Loki posts review → Heimdall shows "Loki found 3 issues, building card…"
- Huginn starts repro → Loki shows "Huginn reproducing…"
- Cross-agent awareness in the room

---

## Priority Recommendation

| Feature | Effort | Impact | Dependencies |
|---------|--------|--------|--------------|
| **Reaction acknowledgments** | Low | High | None (uses existing poster) |
| **Thread-aware typing (WS)** | Medium | High | Typing indicator PRD |
| **Progress updates** | Medium | High | Tool integration points |
| **Rich formatting** | Medium | Medium | Message rendering refactor |
| **Slash commands** | High | Medium | New HTTP endpoint + routing |
| **DM notifications** | Medium | Low | User preference storage |

**Suggested order**:
1. **Reaction acknowledgments** — 2 hours, immediate UX win
2. **Thread-aware typing** — builds on typing PRD
3. **Progress updates** — integrates with tool calls
4. **Rich formatting** — polish for all agent posts

---

## Quick Win: Reaction Acknowledgments (Implementation Sketch)

```python
# agents/_mention_task.py
async def agent_mention_task(...) -> None:
    # ... existing setup ...
    
    # 1. React immediately so user knows mention was received
    await ports.add_reaction(message_id, "eyes")
    
    try:
        # 2. Send typing indicator
        await ports.send_typing(channel, thread_id)
        
        # 3. Process mention
        answer = await _process_mention(...)
        
        # 4. Swap reaction to success
        await ports.remove_reaction(message_id, "eyes")
        await ports.add_reaction(message_id, "white_check_mark")
        
        await _queue_reply(...)
    except Exception:
        await ports.remove_reaction(message_id, "eyes")
        await ports.add_reaction(message_id, "x")
        raise
```

Requires adding `add_reaction`/`remove_reaction` to `AgentPoster` and `AgentRoomPort` — trivial since `SwitchSender` already has them.