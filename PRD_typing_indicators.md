# PRD: Agent Typing Indicators in Switch/Mattermost

## Problem Statement

When a maintainer mentions an agent (@heimdall, @huginn, @loki) in a Switch room, there is **no visual feedback** that the agent has received the message and is processing it. The agent may take 10-60+ seconds to respond (LLM calls, tool usage, GitHub API calls), during which the maintainer sees nothing — no "typing..." indicator, no acknowledgment, no progress signal.

This creates a poor user experience:
- Maintainers don't know if the agent is working or if the mention failed silently
- No way to distinguish "agent is thinking" from "agent is down"
- Maintainers may re-mention the agent, creating duplicate jobs
- No parity with standard chat UX (Slack, Mattermost, Discord all show typing indicators)

## Goals

1. **Show a typing indicator** in the Switch room when an agent starts processing a mention
2. **Keep the indicator active** while the agent is "thinking" (running its LLM loop, calling tools)
3. **Clear the indicator** when the agent posts its reply (or fails)
4. **Work for all three agents** (Heimdall, Huginn, Loki) in both mention and background jobs
5. **Minimal latency** — indicator appears within ~1 second of job pickup
6. **No spam** — indicator clears cleanly, doesn't persist on errors

## Non-Goals

- Per-tool or per-turn granularity (e.g., "calling GitHub API…")
- Typing indicators for outbox-delivered posts (card drafts, review posts, verdicts) — only for interactive mention replies
- Changes to the Switch server — client-side only
- Typing indicators in GitHub PR comments

## User Experience

### Before
```
Maintainer: @heimdall what's the status of #42?
... 30 seconds of silence ...
Heimdall: Here's the status for #42: [...]
```

### After
```
Maintainer: @heimdall what's the status of #42?
[Heimdall is typing...]          ← appears within ~1s
[Heimdall is typing...]          ← stays on while agent thinks
Heimdall: Here's the status for #42: [...]
[typing indicator clears]
```

## Technical Approach

### Mattermost/Switch Typing Indicator Mechanism

Mattermost (which Switch is built on) supports typing indicators via two mechanisms:

1. **WebSocket** (real-time, bidirectional):
   - Client sends: `{"action": "user_typing", "data": {"channel_id": "xxx", "parent_id": "yyy"}, "seq": 1}`
   - Server broadcasts to other users in the channel
   - Indicator auto-clears after ~5 seconds if not refreshed
   - Must be sent repeatedly to keep it visible

2. **REST API** (simpler, fire-and-forget):
   - `POST /api/v4/users/me/teams/{team_id}/channels/{channel_id}/typing`
   - No `parent_id` support for threads (limitation)
   - Shows indicator for ~5 seconds

**Decision**: Use the **REST API** for simplicity and reliability. The agent posts its reply within ~5-60s, so a single REST call at job start is sufficient. If the job runs longer than 5s, we can refresh periodically.

> **Note**: The REST API requires a **Mattermost bot token** (Bearer token), not the Switch agent API key. Agents currently only have Switch agent credentials.

### Architecture Changes

#### 1. Add Mattermost Bot Token to Agent Configuration

Agents need access to a Mattermost bot token for typing indicators (and potentially other REST operations).

```python
# src/heimdall/adapters/switch/agent/config.py
@dataclass(frozen=True)
class AgentConnectionConfig:
    # ... existing fields ...
    mattermost_bot_token: str | None = field(default=None, repr=False)  # NEW
    mattermost_team_id: str | None = field(default=None, repr=False)    # NEW
```

Configuration via environment variables:
```
SWITCH_HEIMDALL_MM_BOT_TOKEN=xxx
SWITCH_HEIMDALL_MM_TEAM_ID=yyy
```

#### 2. Extend `AgentPoster` with Typing Capability

```python
# src/heimdall/adapters/switch/agent/_post.py
class AgentPoster:
    # ... existing methods ...

    async def send_typing(self, channel_id: str, thread_id: str | None = None) -> None:
        """Send a typing indicator for this agent in the given channel/thread."""
        if not self._config.mattermost_bot_token or not self._config.mattermost_team_id:
            return  # Silently skip if not configured
        # POST /api/v4/users/me/teams/{team_id}/channels/{channel_id}/typing
        # Note: Mattermost REST API doesn't support thread_id (parent_id)
```

#### 3. Extend `AgentRoomPort` Protocol

```python
# src/heimdall/adapters/switch/agent/ports.py
class AgentRoomPort(Protocol):
    # ... existing methods ...
    async def send_typing(self, channel_id: str, thread_id: str | None = None) -> None:
        """Send a typing indicator in the given channel/thread."""
        ...
```

#### 4. Send Typing Indicator at Mention Job Start

The typing indicator should be sent when the `agent.mention` job begins processing, **before** the LLM loop starts.

```python
# agents/_mention_task.py
async def agent_mention_task(...) -> None:
    # ... existing setup ...
    channel = room_id or await _room_for(deps.sessions, ctx, pr_ref)
    if channel is None:
        return
    
    # NEW: Send typing indicator immediately
    await ports.send_typing(channel, thread_id)
    
    try:
        ports = DbMentionPorts(deps, ctx, sender_role, sender)
        question = Question(pr_ref=pr_ref, sender=sender, text=text)
        ai = deps.model()
        answer = None
        if deps.approve_by_message is not None:
            answer = await deps.approve_by_message(ports, ctx, ai, question)
        if answer is None:
            answer = await answer_mention(deps.agent, ports, ai, question)
        await _queue_reply(deps.sessions, ctx, own_name, channel, key, answer)
    finally:
        # NEW: Clear typing indicator (Mattermost auto-clears after ~5s, but we can be explicit)
        # For REST API, there's no "stop typing" call — indicator expires naturally
        pass
```

#### 5. Refresh Typing Indicator for Long-Running Jobs

For jobs that may exceed 5 seconds (most LLM runs), we need periodic refresh.

```python
# agents/_mention_task.py
async def _with_typing_indicator(
    poster: AgentPoster,
    channel_id: str,
    thread_id: str | None,
    coro: Callable[[], Awaitable[T]],
) -> T:
    """Run a coroutine while refreshing a typing indicator every 4 seconds."""
    if not poster._config.mattermost_bot_token:
        return await coro()
    
    stop_refresh = asyncio.Event()
    
    async def refresh_loop() -> None:
        while not stop_refresh.is_set():
            await poster.send_typing(channel_id, thread_id)
            try:
                await asyncio.wait_for(stop_refresh.wait(), timeout=4.0)
            except TimeoutError:
                continue
    
    refresh_task = asyncio.create_task(refresh_loop())
    try:
        return await coro()
    finally:
        stop_refresh.set()
        await refresh_task
```

Then wrap the main processing:
```python
answer = await _with_typing_indicator(
    poster=deps.poster,  # Need to add poster to MentionDeps
    channel_id=channel,
    thread_id=thread_id,
    coro=lambda: _process_mention(deps, ctx, channel, question, ai),
)
```

#### 6. Provide `AgentPoster` to Mention Task

The `MentionDeps` needs to carry the `AgentPoster` (or a typing-capable port).

```python
# agents/_mention_ports.py
@dataclass(frozen=True)
class MentionDeps:
    agent: MentionAgent
    sessions: async_sessionmaker[AsyncSession]
    reader: GitHubApiReader
    model: Callable[[], ChatModel]
    guard_act_tools: Callable[[AgentTool], AgentTool]
    approve_by_message: Callable[[MentionPorts, TenantCtx, ChatModel, Question], Awaitable[MentionAnswer | None]] | None = None
    poster: AgentPoster  # NEW: for typing indicators
```

Update all three agent runners to pass the poster.

## Implementation Plan

### Phase 1: Core Infrastructure (1-2 days)
1. Add `mattermost_bot_token` and `mattermost_team_id` to `AgentConnectionConfig`
2. Add `send_typing` to `AgentPoster` (using Mattermost REST API)
3. Add `send_typing` to `AgentRoomPort` protocol
4. Update `SwitchAgentClient` to implement `send_typing` (delegate to poster)

### Phase 2: Mention Task Integration (1 day)
1. Add `poster` to `MentionDeps`
2. Add `_with_typing_indicator` helper in `_mention_task.py`
3. Wrap mention processing with typing indicator
4. Update all three agent runners to build and pass `AgentPoster`

### Phase 3: Background Jobs (Optional, 1 day)
- Add typing indicators to card builds, review jobs, repro jobs
- These run in background without a direct mention, but could show "Heimdall is preparing a card…" in the PR thread

### Phase 4: Testing & Polish (1 day)
- Unit tests for `send_typing` (mock HTTP)
- Integration test: mention → typing appears → reply posted → typing clears
- Verify no indicator spam on errors/timeouts
- Document required environment variables

## Configuration

### Required Environment Variables (per agent)

```bash
# Switch agent credentials (existing)
SWITCH_HEIMDALL_AGENT_ID=...
SWITCH_HEIMDALL_API_KEY=...

# NEW: Mattermost bot token for typing indicators
SWITCH_HEIMDALL_MM_BOT_TOKEN=mm_bot_token_here
SWITCH_HEIMDALL_MM_TEAM_ID=team_id_here
```

The bot token must belong to a bot account that has access to the agent rooms. The team ID is the Mattermost team ID (not the Switch team ID).

### Fallback Behavior

If `mattermost_bot_token` or `mattermost_team_id` is not configured:
- `send_typing` becomes a no-op (logs at DEBUG level)
- Agent works exactly as before, just without typing indicators
- No startup failure — typing is a progressive enhancement

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Mattermost REST API doesn't support thread_id (parent_id) | Typing shows in main channel, not thread. Acceptable for v1. |
| Bot token permissions / rotation | Document token requirements; use dedicated bot per agent. |
| Typing indicator stuck on if job crashes | Mattermost auto-clears after ~5s; no "stop typing" API needed. |
| Extra REST calls add latency | Fire-and-forget; no await on response in hot path (or minimal). |
| Team ID differs per installation | Team ID is global per Mattermost instance; configure once per deployment. |

## Success Metrics

1. **Indicator appears** within 1 second of mention in >95% of cases
2. **Indicator stays visible** for the duration of agent processing (refreshed every 4s)
3. **No false positives** — indicator only on actual mention processing
4. **No errors** in logs from typing indicator calls
5. **Zero impact** on agent response latency

## Rollout Plan

1. Deploy config changes (bot token, team ID) to staging
2. Enable for one agent (Heimdall) first
3. Verify in Switch UI: mention → typing → reply
4. Enable for Huginn and Loki
5. Document in operations guide

## Open Questions

1. **Thread support**: Mattermost REST typing API doesn't support `parent_id`. Should we use WebSocket instead for thread-aware typing?
   - *Decision*: Start with REST (simpler). If thread typing is critical, upgrade to WebSocket in v2.

2. **Shared bot token vs per-agent**: Should all agents share one bot token, or have separate ones?
   - *Decision*: Per-agent tokens (cleaner audit trail, separate rate limits).

3. **Team ID discovery**: Can we derive team ID from room ID via API?
   - *Decision*: Configure explicitly for v1. Auto-discovery adds complexity.

4. **Typing for outbox posts**: Should card drafts, reviews, verdicts show typing?
   - *Decision*: Out of scope for this PRD. They're asynchronous; typing would be misleading.

## Dependencies

- Mattermost bot account(s) with `post:all` and `channel:read` permissions in agent rooms
- Network connectivity from agent workers to Mattermost REST API (same as SwitchSender)
- No code changes required in Switch server

## Appendix: Mattermost Typing API Reference

**Endpoint**: `POST /api/v4/users/me/teams/{team_id}/channels/{channel_id}/typing`

**Headers**: `Authorization: Bearer {bot_token}`

**Response**: 200 OK with empty body

**Effect**: Shows "X is typing..." in the channel for ~5 seconds. No `parent_id` parameter for threads.

**WebSocket Alternative** (for v2):
```json
{
  "action": "user_typing",
  "data": {
    "channel_id": "channel_id",
    "parent_id": "root_post_id"  // for threads
  },
  "seq": 1
}
```
Sent over the Mattermost WebSocket (`/api/v4/websocket`).