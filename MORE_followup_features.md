# More Follow-up Features: Deep Cuts & Power User Tools

---

## 11. **Ephemeral "Thinking" Posts (Updatable Messages)**
Mattermost allows updating posts. Instead of typing indicator + final reply, post one message and update it:
```
Heimdall: 🔄 Analyzing PR #42…
Heimdall: 🔄 Found 3 changed files, fetching diffs…
Heimdall: 🔄 Running Loki review…
Heimdall: ✅ Review complete — 2 findings, 1 suggestion
```
**Benefit**: Single message, full history visible, works in threads, no typing API needed.

---

## 12. **Agent "Busy" Presence Status**
Set agent's Mattermost status to "Away/Busy" when queue depth > threshold:
- Shows red dot on agent name in channel sidebar
- Maintainers see "Heimdall is busy (3 jobs queued)" before mentioning
- Auto-clears when queue drains

---

## 13. **Structured Command Palette (Typeahead)**
Mattermost supports custom slash command suggestions:
```
@heimdall /card       # Build approval card
@heimdall /repro      # Trigger reproduction  
@heimdall /review     # Request Loki review
@heimdall /status     # PR status summary
@heimdall /config     # Show repo config
```
**Benefit**: Discoverable, reduces "help" questions, typo-resistant.

---

## 14. **Thread Summarization on Demand**
`@heimdall summarize` in a long thread → posts a concise summary:
- Key decisions made
- Open questions
- Action items
- Links to GitHub artifacts

---

## 15. **Cross-Reference Link Unfurling**
When agent posts GitHub URLs (PRs, issues, commits, files), unfurl them as rich cards:
- PR: title, status, author, checks
- Issue: title, state, labels
- Commit: message, checks, files changed
- File: path, language, snippet

---

## 16. **Interactive Buttons on Agent Replies**
Attach actions to agent messages:
```
[View Diff] [Approve Card] [Request Changes] [Open in GitHub]
[Re-run Tests] [Add Finding] [Dismiss]
```
Callbacks route back to agent via webhook → new mention job.

---

## 17. **Scheduled / Reminder Messages**
`@heimdall remind me in 2h to check #42` → agent DMs maintainer later.
`@heimdall ping when checks pass on #42` → agent monitors GitHub, notifies.

---

## 18. **Multi-Room / Multi-Repo Awareness**
Agent knows all rooms it's in. Mention in any room:
- `@heimdall status` → shows status for PR linked to *that* room
- `@heimdall status org/repo#42` → explicit cross-repo query
- Room-specific config (different models, prompts, permissions per room)

---

## 19. **Agent-to-Agent Mentions in Switch**
`@heimdall ask @loki to review the new auth module` → Heimdall queues Loki job, posts result.
Enables maintainer to orchestrate without leaving chat.

---

## 20. **Voice / Audio Transcription**
Mattermost supports voice messages. Agent could:
- Transcribe voice mention → process → reply (text or voice)
- Useful for mobile maintainers

---

## 21. **Code Snippet Sharing with Syntax Highlighting**
Agent posts code blocks as Mattermost snippets (not inline):
- Full file context
- Line numbers
- Language detection
- Downloadable

---

## 22. **Reaction-Based Voting on Findings**
Loki posts findings with 👍/👎 reactions:
- Maintainers vote on relevance
- Agent learns from feedback (future prompt tuning)
- Auto-dismiss findings with >3 👎 from maintainers

---

## 23. **Private Thread for Sensitive Discussion**
`@heimdall private` → creates a private channel with maintainer + agent:
- Discuss security issues, credentials, secrets
- Not visible in public PR room
- Auto-archives after inactivity

---

## 24. **Webhook Relay for External Tools**
Agent posts can trigger external webhooks:
- "Deploy to staging" button → calls CI/CD webhook
- "Create Jira ticket" → creates issue with PR context
- Configurable per-repo in `heimdall.yml`

---

## 25. **Agent Analytics Dashboard (in Switch)**
`@heimdall stats` → posts a formatted table:
```
┌─────────────┬────────┬──────────┬──────────┐
│ Metric      │ 24h    │ 7d       │ 30d      │
├─────────────┼────────┼──────────┼──────────┤
│ Mentions    │ 47     │ 312      │ 1,204    │
│ Avg latency │ 12.3s  │ 11.8s    │ 12.1s    │
│ Success %   │ 94%    │ 96%      │ 95%      │
│ Tokens/day  │ 2.1M   │ 1.9M     │ 1.8M     │
└─────────────┴────────┴──────────┴──────────┘
```

---

## 26. **Custom Emoji Status per Agent**
Register custom emoji for each agent state:
- `:heimdall-thinking:` (animated)
- `:huginn-testing:` (spinner)
- `:loki-reviewing:` (magnifying glass)
Shows in typing indicator area.

---

## 27. **Markdown Export / Conversation Log**
`@heimdall export #42` → DMs maintainer a Markdown file of:
- All agent mentions in the PR thread
- Replies, findings, decisions
- Clean format for retrospectives, audits, handoffs

---

## 28. **Agent "Personality" Modes**
Configurable tone per repo/room:
- `terse` — minimal, just facts
- `friendly` — conversational, explains reasoning
- `pedantic` — cites every line, exhaustive
- `coach` — teaches, suggests improvements

---

## 29. **Offline Queue Indicator**
If Switch connection drops, agent queues mentions locally.
On reconnect: posts "📬 Processed 3 queued mentions while offline" summary.

---

## 30. **Mention Analytics per Maintainer**
`@heimdall my-stats` → shows maintainer's own usage:
- Questions asked, by agent
- Most common topics
- Response time they experience
- "You ask about testing 40% of the time"

---

## Wild Ideas (Probably Too Much)

| Idea | Why It's Cool | Why It's Hard |
|------|---------------|---------------|
| **Agent avatar changes** (busy/idle/error) | Visual status at a glance | Mattermost doesn't support dynamic bot avatars easily |
| **Collaborative editing** (agent + human edit same doc) | Real-time PR description fixes | Requires shared editor integration |
| **Voice replies** (TTS) | Accessibility, hands-free | Audio hosting, playback in Mattermost |
| **Agent marketplace** (install community agents) | Extensibility | Security, sandboxing, versioning |
| **Time-travel debugging** (replay agent reasoning) | Understand why agent did X | Storage, privacy, UI |

---

## Implementation Path (If You Want More)

After typing indicators + reactions + progress:

1. **Ephemeral thinking posts** — replaces typing, more informative
2. **Interactive buttons** — turns read-only replies into actions
3. **Command palette** — discoverability
4. **Rich unfurling** — GitHub links become useful cards
5. **Scheduled reminders** — async workflows

Each builds on the same `AgentPoster` / `SwitchSender` foundation.