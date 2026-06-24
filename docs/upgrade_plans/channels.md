# Channels — Remote AI + Developer Marketing Engine

## The Pitch

**Replace the $30–$200/month Fiverr social media manager** with an AI that runs on your machine, knows your project intimately, and works while you sleep.

Solo developers and small teams can't afford (or don't want to manage) a human SMM, but need the same outcomes: consistent posting, community replies, brand presence, and engagement growth. Kognisant Channels delivers this at $0/month + local compute.

| What a Fiverr SMM Does | Monthly Cost | Kognisant Channels |
|------------------------|-------------|-------------------|
| Content creation (posts, threads) | $30–$150 | ✅ LLM-generated from your docs, commits, topics |
| Content scheduling (calendar, optimal times) | Included | ✅ Cron + priority queue |
| Community engagement (reply to comments/DMs) | $50–$200 | ✅ Template bank + LLM for novel questions |
| Brand monitoring (mentions, tags) | $30–$100 | ✅ Adapter polling |
| Analytics & reporting | $50–$150 | ✅ Local actionable metrics |
| Crisis management (escalate negatives) | $100+ | ✅ Escalation queue |
| Cross-platform management | $100–$300 | ✅ Phase 3+ |

**Plus what no human SMM gives you:**
- Remote AI assistant access from any messaging platform
- Full project context in every response (knows your codebase, docs, issues)
- Learns what your community asks → suggests documentation improvements
- Zero data leaves your machine (local models, self-hosted)

---

## The Two Modes

### Mode 1: Remote Assistant

The user messages their Kognisant bot from their phone/another device. The message reaches their local machine, gets processed with full project context, tools, memory, and PERP agents, then responds through the same channel.

```
User on phone (Telegram/WhatsApp/Discord/X DM)
        │
        ▼
┌─────────────────────────────────┐
│  Channel Adapter (script)        │
│  Running inside kognisant daemon │
└───────────────┬─────────────────┘
                │ Unix domain socket
                ▼
┌─────────────────────────────────────────────┐
│  Kognisant Core (user's PC/laptop/server)    │
│                                               │
│  ├─ Full chat.py pipeline (all tools)         │
│  ├─ Project memory + context.md               │
│  ├─ PERP agents for complex tasks             │
│  ├─ World model + self-model                  │
│  └─ Model pool (local + cloud)                │
└───────────────┬─────────────────────────────┘
                │
                ▼
        Response sent back via same channel
```

**What this enables:**
- Full project awareness (not generic Q&A — knows YOUR codebase)
- Tool execution on your machine (run builds, edit files, check git remotely)
- Agent swarms from your phone ("refactor the auth module" via Telegram → PERP)
- Persistent memory across all channels (same brain via CLI or Telegram)
- Job management remotely ("/jobs" via Discord DM)

### Mode 2: Developer Marketing Engine (SMM Replacement)

Kognisant autonomously operates social accounts: posting content, replying to community, moderating, scheduling — with project-aware intelligence.

```
Platform Events (mentions, DMs, comments)
        │
        ▼
┌─────────────────────────────────┐
│  Channel Adapter (script)        │
└───────────────┬─────────────────┘
                │ Unix domain socket
                ▼
┌─────────────────────────────────────────────┐
│  SMM Engine (in-process, daemon-managed)      │
│                                               │
│  ├─ Priority Queue (P0-P4)                    │
│  ├─ Template Bank (instant, zero-cost)        │
│  ├─ manager_respond() (LLM, zero tools)       │
│  ├─ Thread State DB (SQLite)                  │
│  ├─ Content Calendar (cron-triggered)         │
│  ├─ Moderation Pipeline                       │
│  ├─ Escalation Router                         │
│  └─ Analytics Sink (local metrics)            │
└───────────────┬─────────────────────────────┘
                │
                ▼
        Action sent back via adapter
```

### Hybrid: Both on One Channel

A single Telegram bot serves BOTH purposes:
- DMs from the **owner** → Remote assistant mode (full Kognisant, bypasses all queues)
- Messages from **everyone else** → SMM engine (templates, LLM queue, moderation)

The `ChannelRouter` is a **state machine**, not a simple if-statement. In hybrid mode, routing depends on both sender AND context:

```python
class ChannelRouter:
    def route(self, event: ChannelEvent, channel: ChannelConfig) -> RouteDecision:
        if channel.mode == "assistant":
            return RouteDecision.ASSISTANT
        if channel.mode == "manager":
            return RouteDecision.MANAGER

        # Hybrid mode — routing depends on sender + context
        if self.is_owner(event.sender_id, channel.owner_ids):
            if event.event_type == "dm":
                return RouteDecision.ASSISTANT       # Private DM = full access
            if event.content.startswith("/"):
                return RouteDecision.ASSISTANT       # Public @bot with slash command
            return RouteDecision.MANAGER             # Public mention without / = manager voice

        return RouteDecision.MANAGER
```

**Key rule**: In hybrid mode, the owner retains assistant access in public spaces via `/` prefix. "@mybot /agent fix tests" in a public channel → assistant mode. "@mybot nice work!" → manager voice (stays in character).

---

## The Queue Is the Product

### Why Async Is Correct

Human SMMs don't reply instantly. They batch-check mentions every few hours and write thoughtful responses. Users expect async, not real-time. With local models (single GPU, ~10-30 tokens/sec, 5-15s per response), you can't parallelize — you must queue.

**The queue is not a bug — it's the feature.** Communicate it:

```
[User sets up X channel in manager mode]

Kognisant: "Manager mode active. I'll check mentions every 15 minutes and 
queue responses. Expect replies within 1-2 hours during active hours.

Queue depth: 0 | Budget remaining: 45/50 LLM calls today."
```

### Priority Queue Design

```
┌─────────────────────────────────────────────────────────────┐
│  SMM Response Queue (per channel, in-process)                │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  P0: Scheduled content posts                                  │
│      → Must fire at exact cron time (calendar integrity)      │
│      → PREEMPTS all lower priorities                          │
│      → Missed = calendar broken = user trust broken           │
│                                                               │
│  P1: Escalations (human review required)                      │
│      → Skip LLM entirely, notify owner immediately            │
│      → Never queued, never delayed                            │
│                                                               │
│  P2: Reply to mentions (within active hours)                  │
│      → Template match = instant (zero LLM cost)               │
│      → No template = enqueue for LLM                          │
│      → Deadline: 2 hours (drop if expired)                    │
│                                                               │
│  P3: Thread follow-ups (in-conversation)                      │
│      → Need thread context from DB                            │
│      → Deadline: 4 hours (less urgent)                        │
│                                                               │
│  P4: Content pre-generation (overnight batch)                 │
│      → Generate tomorrow's posts while idle                   │
│      → No deadline, processed when queue empty                │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

**Key**: Scheduled content (P0) preempts replies (P2). If a cron fires while processing mentions, the mention pauses and the post goes out on time. "I said post at 9am, it posts at 9am."

**Preemption semantics**: P0 preempts at **enqueue time, not execution time**. If a P2 LLM call is in-flight when P0 arrives, the LLM call finishes (don't cancel mid-inference — wastes tokens, may hang connection). P0 goes next immediately after. If P0's scheduled time is missed by >60s due to an in-flight call, log a warning. In practice, even a 15-second LLM call means <15s delay on a scheduled post — acceptable.

### Active Hours

A human SMM doesn't work 24/7. Your bot shouldn't either:

```json
"schedule": {
  "active_hours": "09:00-22:00",
  "timezone": "America/New_York",
  "off_hours_behavior": "queue_for_morning"
}
```

**Active hours rules (explicit):**

- **P0 (scheduled posts)**: IGNORE active hours. Posts fire at their cron time regardless.
- **P1 (escalations)**: IGNORE active hours. Owner is always notified.
- **P2-P4 (replies, threads, pre-gen)**: Soft filter.
  - New P2-P4 jobs arriving after active hours → held in "morning queue", released at 09:00.
  - In-flight P2-P4 jobs when active hours end → continue processing until their deadline or completion (whichever first). Don't abort mid-reply.
  - Jobs queued before 22:00 with deadline 23:55 → still valid, process if capacity exists. Active hours ending doesn't kill existing deadlines.

This saves LLM budget (no 3am replies nobody sees) and matches how solo devs actually work.

### Cost Gate: Template Fallback with Hysteresis

When LLM budget is exhausted, switch to template-only mode — don't stop the channel:

```json
"cost_gate": {
  "max_llm_calls_per_hour": 10,
  "max_llm_calls_per_day": 50,
  "on_exceeded": "template_only"
}
```

**Hysteresis**: Once triggered, stay in template-only mode for the **full remaining hour**. Don't flap between LLM and template modes if the counter resets mid-burst:

```python
class CostGate:
    def __init__(self, hourly_limit: int, daily_limit: int):
        self.hourly_limit = hourly_limit
        self.daily_limit = daily_limit
        self.template_only_until: datetime | None = None

    @property
    def exhausted(self) -> bool:
        if self.template_only_until and datetime.now(timezone.utc) < self.template_only_until:
            return True  # Still in cooldown
        if self._hourly_count() >= self.hourly_limit:
            # Triggered — lock out for remainder of hour
            self.template_only_until = self._next_hour_boundary()
            return True
        if self._daily_count() >= self.daily_limit:
            self.template_only_until = self._next_day_boundary()
            return True
        return False
```

With a daily budget of 50 LLM calls: 3 posts + 7 replies + 40 reserve. Sustainable on a local 7B model.

### Queue Implementation

```python
class ResponseQueue:
    """Per-channel priority queue with deadline enforcement."""

    def __init__(self, channel_name: str, max_llm_concurrent: int = 1):
        self.queues: dict[int, deque] = {p: deque() for p in range(5)}
        self.max_llm_concurrent = max_llm_concurrent  # 1 for local models
        self.active_llm_jobs = 0
        self.template_bank = TemplateBank(channel_name)
        self.thread_db = ThreadStateDB(channel_name)
        self.cost_gate = CostGate(channel_name)

    def enqueue(self, event: ChannelEvent, priority: int, deadline: datetime | None):
        job = {
            "id": generate_uuid(),
            "event": event,
            "priority": priority,
            "deadline": deadline,
            "thread_id": event.reply_to or event.context.get("thread_id"),
        }
        self.queues[priority].append(job)

    def process_next(self) -> ChannelAction | None:
        """Called by daemon poll. Returns one action or None."""
        self._drop_expired()

        for p in sorted(self.queues.keys()):
            if not self.queues[p]:
                continue
            job = self.queues[p][0]

            # P0: Scheduled posts — always execute immediately
            if p == 0:
                return self._execute_scheduled_post(self.queues[p].popleft())

            # P1: Escalations — notify owner, no LLM
            if p == 1:
                return self._escalate(self.queues[p].popleft())

            # P2+: Try template first
            template = self.template_bank.match(job["event"].content)
            if template:
                self.queues[p].popleft()
                return ChannelAction(action_type="reply", content=template, ...)

            # Need LLM — check budget and concurrency
            if self.cost_gate.exhausted:
                continue  # Skip LLM items when budget gone (templates still work)
            if self.active_llm_jobs >= self.max_llm_concurrent:
                return None  # LLM busy

            self.queues[p].popleft()
            self.active_llm_jobs += 1
            try:
                thread_ctx = self.thread_db.get_thread(job.get("thread_id"))
                response = manager_respond(job["event"], self.config, thread_ctx)
                self.cost_gate.record_call()
                return ChannelAction(action_type="reply", content=response, ...)
            finally:
                self.active_llm_jobs -= 1

    def _drop_expired(self):
        now = datetime.now(timezone.utc)
        for p in range(2, 5):
            while self.queues[p] and self.queues[p][0].get("deadline"):
                if now > self.queues[p][0]["deadline"]:
                    self._log_dropped(self.queues[p].popleft())
                else:
                    break
```

---

## Content Calendar: The Core Value

Fiverr gigs emphasize "I'll create a content calendar and post 3x/day." Replies are secondary. The calendar is the primary feature.

### Daily Flow

```
Morning (9am):
  1. Bot reads docs/content-plan.md (user's topic list)
  2. Bot reads recent commits/issues (project activity)
  3. Bot generates post via LLM
  4. Post enters queue at P0 → fires at exact scheduled time

Afternoon (2pm):
  1. Bot reads trending topics in niche (if configured)
  2. Generates thread/post
  3. Queued at P0

Evening (6pm):
  1. Bot checks mentions accumulated during the day
  2. Template matches → instant reply
  3. Novel questions → queued for LLM at P2
  4. Processed sequentially (1 LLM call at a time)

Overnight (idle):
  1. Pre-generate tomorrow's posts (P4, lowest priority)
  2. Analyze conversation logs → suggest new templates
```

### Content Generation Pipeline

```
1. Cron fires at configured time
2. Bot reads content source:
   - docs/content-plan.md (topic list)
   - Recent git commits (project activity)  
   - Recent issues/PRs (community interest)
   - Changelog entries (release notes)
3. LLM generates post with persona voice
4. Output validation (TOS, length, tone)
5. Platform-specific formatting
6. Post queued at P0 → fires on time
```

**One LLM call per post.** With 50 daily calls: 3 posts + 47 replies is sustainable.

### Cross-Platform Content Adaptation

One content piece, generated **per-platform** (one LLM call each). This allows platform-specific tuning and independent retries:

```
Source topic: "Kognisant v2.1 released with remote assistant support"

Call 1 (X, 280 chars, casual):
  "🚀 Kognisant v2.1 — remote AI assistant via Telegram, Discord, WhatsApp.
   Your machine, your models, zero deps.
   pip install --upgrade kognisant
   #opensource #ai #cli"

Call 2 (LinkedIn, 3000 chars, professional):
  "Excited to announce Kognisant v2.1 with remote assistant capabilities. 
   Developers can manage projects from anywhere while keeping all data local..."

Call 3 (Discord, embeds allowed, community tone):
  "@everyone **Kognisant v2.1 is live!** Remote AI assistant support.
   See #changelog for details."
```

**Why per-platform, not single call?**
- Each platform has wildly different constraints (280 chars vs 3000 vs embeds)
- Platform-specific prompts can be tuned independently
- One platform failing doesn't block others
- Easier to debug and A/B test per platform

Optimize to single-call later if token cost becomes a problem. Clarity beats efficiency at this stage.

### Approval Queue (Training Wheels)

For risk-averse users:

```json
"content_calendar": {
  "require_approval": true,
  "approval_deadline_hours": 1
}
```

Generated content queues for owner review. Owner gets batch notification:
"3 posts pending — /approve_all | /reject 2 | /edit 1 'new text'"

If deadline passes without action → content dropped (not auto-posted). Once trust is built, disable approval mode.

---

## Template Response Bank

Most social interactions are repetitive. Templates handle ~70% of mentions at zero LLM cost, instant response time.

**Matching rules:**
- Templates sorted by `hit_count` descending — most-used patterns checked first
- Regex + keyword scoring (not LLM classification)
- **ReDoS protection**: all patterns validated at add-time:
  ```python
  def validate_template_pattern(pattern: str) -> None:
      """Reject dangerous regexes before they enter the bank."""
      compiled = re.compile(pattern)
      if compiled.groups > 5:
          raise ValueError("Too many capture groups — simplify")
      # Test for catastrophic backtracking
      test_input = "a" * 1000
      start = time.monotonic()
      re.search(pattern, test_input)
      if time.monotonic() - start > 0.01:  # 10ms threshold
          raise ValueError("Pattern too slow — ReDoS risk")
  ```

### Structure

```json
{
  "templates": [
    {
      "intent": "installation",
      "patterns": ["how (do|can|to) (install|setup|get started)", "pip install"],
      "response": "Hey! Quick install: `pip install {project_name}` — full guide at {docs_url}/getting-started 🔧"
    },
    {
      "intent": "licensing",
      "patterns": ["(is this|is it) free", "license", "open source", "can I use"],
      "response": "{project_name} is {license} and free to use. {repo_url}"
    },
    {
      "intent": "version_inquiry",
      "patterns": ["when is (v2|next version|next release)", "roadmap", "upcoming"],
      "response": "Tracking progress at {issue_url}. No fixed ETA but actively developing! ⚡"
    },
    {
      "intent": "bug_report",
      "patterns": ["found a bug", "not working", "broken", "error", "crash"],
      "response": "Thanks for reporting! Please open an issue at {repo_url}/issues with repro steps and we'll look into it. 🐛"
    },
    {
      "intent": "comparison",
      "patterns": ["vs (OpenClaw|Aider|Claude Code|Cursor)", "compared to", "alternative"],
      "response": "Great question! Here's a quick comparison: {comparison_url}. TL;DR: {project_name} runs fully local with persistent memory and zero dependencies."
    }
  ],
  "variables": {
    "project_name": "Kognisant",
    "license": "MIT licensed",
    "docs_url": "https://kognisant.dev/docs",
    "repo_url": "https://github.com/user/kognisant",
    "issue_url": "https://github.com/user/kognisant/issues",
    "comparison_url": "https://kognisant.dev/compare"
  }
}
```

### Auto-Template Discovery

Batched weekly (not per-template interruptions):

```
📋 Template Suggestions (week of 2026-06-24):
  [1] "install_question" — asked 8x, similar LLM responses each time
  [2] "pricing_question" — asked 5x, consistent replies
  [3] "windows_support" — asked 5x, NO TEMPLATE EXISTS

  Run: kognisant channel templates brand-x --accept 1 2 3
  Or:  /template_add 1 2 3 (via channel)
```

Suggestions generated from conversation logs — when the same question triggers 3+ LLM responses with high similarity, it's a template candidate. This progressively reduces LLM usage as the bot learns your community's FAQ.

### Competitive Mention Tracking

```json
"competitive_tracking": {
  "competitors": ["OpenClaw", "Claude Code", "Aider", "Cursor"],
  "on_mention": "queue_with_competitive_prompt",
  "sentiment_gate": true
}
```

When someone mentions a competitor alongside your project:
```
"Has anyone used Kognisant vs OpenClaw?"
→ Bot detects competitor mention
→ Sentiment check: neutral/positive? → queue competitive response
→ Sentiment check: negative ("both are sketchy")? → ESCALATE to owner, don't auto-reply
→ Reply: "Both are solid! Key differences: Kognisant runs fully on your machine
   with persistent memory and zero deps. OpenClaw needs Docker + Node.js.
   Comparison: {url}"
```

**Sentiment gate**: If the mention contains negative language about YOUR project ("garbage", "broken", "scam", "waste"), escalate to owner instead of auto-replying. Defensive or tone-deaf competitive responses do more damage than silence.

---

## Architecture

### How It Fits Into Existing Kognisant

```
┌──────────────────────────────────────────────────────────────────────────┐
│                      CHANNELS SYSTEM                                       │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  ChannelRouter                         SMM Engine                          │
│  ─────────────                         ──────────                          │
│  is_owner(sender)?                     ResponseQueue (P0-P4)               │
│    YES → chat.py (full tools)          TemplateBank (zero-cost replies)    │
│    NO  → SMM Engine                    ContentCalendar (cron-triggered)    │
│                                        ThreadStateDB (SQLite)              │
│  ChannelManager                        ModerationPipeline                  │
│  ──────────────                        CostGate (budget enforcement)       │
│  create/remove/list                    EscalationRouter                    │
│  start/stop/pause                      AnalyticsSink (local metrics)       │
│  credential decrypt → env inject       manager_respond() (zero tools)      │
│                                                                            │
└────────────────────────────────┬───────────────────────────────────────────┘
                                 │
         ┌───────────────────────┼───────────────────────┐
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐  ┌───────────────────┐  ┌───────────────────────────┐
│  chat.py         │  │  daemon.py         │  │  Adapter Scripts           │
│  (full pipeline  │  │  (lifecycle mgmt,  │  │  (per-platform, own venv)  │
│   for owner)     │  │   UDS server,      │  │  channel_telegram.py       │
│                  │  │   poll loop)        │  │  channel_x.py              │
└─────────────────┘  └───────────────────┘  └───────────────────────────┘
```

### manager_respond(): Zero-Tool Enforcement

Manager mode uses a **completely separate function** from `chat.py`. This is enforcement, not policy:

```python
def manager_respond(event: ChannelEvent, config: dict, thread_ctx: list | None) -> str:
    """Generate manager-mode response. Structurally CANNOT execute tools.
    
    Receives ONLY: persona, knowledge_base content, conversation thread.
    Returns: plain text response.
    NO tools registered. NO function calling. NO project file access.
    """
    messages = [
        {"role": "system", "content": build_manager_system_prompt(config)},
        *(thread_ctx or []),
        {"role": "user", "content": event.content},
    ]
    
    # LLM call with NO tools parameter
    response = query_model_api(
        api_base_url=..., model=..., messages=messages
        # tools deliberately omitted
    )
    
    if not validate_outbound(response, config):
        return None  # Failed validation → escalate
    return response
```

**Why separate from chat.py?** `chat.py` has tool infrastructure baked in. Even with empty tools list, future refactors could re-enable them. `manager_respond()` structurally cannot call tools.

---

## Adapters = Scripts

### Why Scripts

Adapters are standalone Python scripts in `~/.kognisant_core/scripts/`, each with its own virtualenv. This means:

1. **Unrestricted dependencies** — `python-telegram-bot`, `tweepy`, `discord.py`, whatever the platform needs
2. **Kognisant core stays stdlib-only** — adapters are subprocesses, not imports
3. **Existing infrastructure reused** — `ProcessManager`, crash restart, PID tracking, log rotation
4. **Agents can generate custom adapters** — PERP writes adapters for niche platforms
5. **User-customizable** — edit scripts directly for custom logic
6. **Core handles crypto** — credentials decrypted by core, passed to adapter via env vars

### IPC: Unix Domain Sockets

Adapters communicate via UDS (not stdin/stdout pipes). This solves buffer deadlock, interleaving, and backpressure:

- **Socket path**: `/tmp/kognisant_channel_{name}.sock`
- **Wire format**: Length-prefixed binary (`struct.pack(">I", len) + JSON payload`)
- **Multiplexing**: `select.poll()` (not `select.select()` — avoids macOS fd limits)
- **Still stdlib**: `socket.AF_UNIX` + `select.poll()` (Python 3.10+)

### Protocol v1.0

**Handshake:**
```
Adapter connects → sends:  {"type": "hello", "protocol_version": "1.0", "adapter_version": "0.2.0"}
Daemon responds:           {"type": "hello_ack", "protocol_version": "1.0", "config_path": "/tmp/..."}
Adapter reads config →:    {"type": "ready", "capabilities": ["message", "dm", "reply", "post"]}
```

**Adapter → Daemon:**
```json
{"type": "event", "event_id": "...", "platform": "x", "event_type": "mention", "sender_id": "x:123", "content": "...", "timestamp": "...", "context": {...}}
{"type": "heartbeat", "seq": 47, "ts": "..."}
{"type": "action_result", "action_id": "act_001", "success": true}
```

**Daemon → Adapter:**
```json
{"type": "action", "action_id": "act_001", "action_type": "reply", "target_id": "...", "content": "...", "metadata": {...}}
{"type": "action", "action_id": "act_002", "action_type": "post", "content": "...", "metadata": {...}}
{"type": "heartbeat_ack", "seq": 47}
{"type": "config_reload", "config_path": "/tmp/..."}
{"type": "shutdown", "grace_seconds": 5}
```

**Bidirectional heartbeat:** Both sides send every 30s with seq numbers. 3 missed = connection dead → daemon restarts adapter.

**Version negotiation:** Major version mismatch → reject. Minor mismatch → compatibility mode.

### Config & Credential Delivery

```bash
# Daemon writes config, injects decrypted credentials as env vars:
KOGNISANT_SOCKET=/tmp/kognisant_channel_my-bot.sock \
KOGNISANT_CONFIG_PATH=/tmp/kognisant_cfg_my-bot.json \
KOGNISANT_X_API_KEY=<decrypted> \
KOGNISANT_X_API_SECRET=<decrypted> \
~/.kognisant_core/scripts/channel_x_venv/bin/python channel_x.py
```

**Adapter never touches encrypted files.** Core decrypts → env var → dies with process.

### Reference Adapters (Pre-Shipped, Tested)

| Platform | Script | Dependencies | Connection |
|----------|--------|-------------|-----------|
| Telegram | `channel_telegram.py` | `python-telegram-bot` | Long polling |
| X/Twitter | `channel_x.py` | `tweepy` | API v2 polling |
| Discord | `channel_discord.py` | `discord.py` | WebSocket gateway |
| Reddit | `channel_reddit.py` | `praw` | API polling |
| Webhook | `channel_webhook.py` | `cryptography` | HTTP listener |

Agent-generated adapters for custom platforms (Mastodon, Bluesky, Slack, etc.).

---

## Thread State Database

Each channel gets a SQLite database for conversation tracking and metrics:

```sql
-- ~/.kognisant_core/channels/sessions/{name}/state.db
-- IMPORTANT: Open with WAL mode for concurrent read/write safety

-- Connection setup (every open):
-- PRAGMA journal_mode=WAL;
-- PRAGMA synchronous=NORMAL;
-- PRAGMA busy_timeout=5000;

CREATE TABLE threads (
    thread_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_activity TEXT NOT NULL,
    message_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active'  -- active | resolved | escalated | abandoned
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT REFERENCES threads(thread_id),
    role TEXT NOT NULL,       -- 'user' | 'bot'
    content TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    platform_msg_id TEXT
);

CREATE TABLE metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    metric_type TEXT NOT NULL,  -- response_sent | template_hit | llm_call |
                               -- dropped_deadline | escalation | post_published
    value REAL,
    metadata TEXT              -- JSON blob
);

CREATE TABLE templates_usage (
    template_id TEXT NOT NULL,
    used_at TEXT NOT NULL,
    hit_count INTEGER DEFAULT 1,
    event_content TEXT         -- What triggered the match (for auto-template analysis)
);
```

Retention: messages >30 days auto-pruned. Metrics kept 90 days.

**Threading constraint:** `sqlite3` connections cannot be shared across threads. If the SMM engine moves to a background thread (Phase 2c), use a single writer thread with a `queue.Queue` or re-open the connection per-thread. Document and enforce this from Phase 2a.

---

## Local Analytics (Actionable, Not Vanity)

No platform API needed. Computed from SQLite. **Cached for 60 seconds** (don't re-query on every CLI call during launch day).

```bash
$ kognisant channel metrics brand-x           # Last 7 days (default)
$ kognisant channel metrics brand-x --live    # Last 1 hour (real-time during launches)

📊 Weekly Report: brand-x (X/Twitter)
─────────────────────────────────────
Content Published:     21 posts | 3 threads
Mentions Received:     47
Replies Sent:          41 (87% response rate)
  Template hits:       28 (68%)
  LLM responses:       13 (32%)
Queue Max Depth:       3
Avg Response Time:     1.4 hours
Dropped (deadline):    4 (8.5%)
Escalations:           2 (1 resolved, 1 pending)
LLM Budget Used:       38/350 weekly (11%)

📌 Insights:
  "How to install?" asked 8x → template working well ✅
  "Windows support?" asked 5x → NO TEMPLATE (add one or update docs)
  "vs CompetitorX?" asked 3x → competitive response opportunity
  Response time trending DOWN ↓ (templates learning)
```

**This is more valuable than impression counts.** It tells you:
- What to document (FAQ gaps)
- What to build (feature requests showing up as mentions)
- Where confusion lives (repeated questions)
- Whether the bot is keeping up (queue depth, drop rate)

---

## Security Model

### Threat Surface

```
REMOTE ASSISTANT MODE              SMM ENGINE MODE
────────────────────               ───────────────
T1: Platform account hijack        T5: Prompt injection via public messages
T2: Webhook adapter spoofing       T6: Reputation attack (trick bot into harmful content)
T3: Stolen device (active session) T7: Data exfiltration (leak project info publicly)
T4: Credential file compromise     T8: Rate limit exhaustion / cost attack
```

### Remote Assistant Security (6 Layers)

```
Layer 1: Platform Identity
    sender_id must match owner_ids[] (platform-native, unforgeable)

Layer 2: Session Auth (optional, recommended for high-security)
    Owner sends PIN/passphrase → session expires after 8h (configurable)
    Anomaly detection: unusual hours/rate → force re-auth

Layer 3: Action Confirmation
    Destructive ops (delete, shell) require challenge-response (4-digit code)
    Even within active session, ALWAYS_CONFIRM ops need fresh confirmation

Layer 4: Tool Allowlist
    Per-channel: allow_file_ops, allow_shell, allow_agent_swarm
    
Layer 5: Audit Trail
    Every remote action logged: who, when, what, result

Layer 6: Kill Switch
    /lockdown → immediately stops ALL channel adapters
    kognisant channel revoke-sessions → invalidates all active sessions
```

### SMM Engine Security

- **Manager mode has ZERO access to project files/memory/tools** (structural, not policy)
- **Output validation before every send**: scan for file paths, API keys, tokens, injection echoes
- **Canary string in system prompt**: if response contains it → prompt leaked → drop + escalate
- **No function calling in manager LLM calls**: text generation only, structurally enforced
- **Knowledge base is explicitly opt-in**: bot only knows what you designate as public docs
- **Conversation depth limit**: manager sees last 5 messages per thread (prevents context manipulation)
- **Content blocklist**: hard-coded topics never responded to (politics, medical, legal, financial advice)

### Credential Handling

- Encrypted at rest: PBKDF2 (600K iterations) → AES-256-GCM (via `cryptography` package)
- **Tiered with hard failure (no obfuscation fallback):**
  ```
  Priority 1: cryptography package (AES-256-GCM)
  Priority 2: OS keyring (macOS: security CLI, Linux: secret-tool)
  Priority 3: REFUSE TO STORE. Channel setup aborts.
  ```
  If neither is available:
  ```
  Error: No secure credential storage available.
    Option 1: pip install cryptography
    Option 2: Configure OS keyring (macOS Keychain / GNOME Keyring)
  Channel setup aborted. Credentials NOT stored.
  ```
  **No XOR obfuscation. No "store anyway with warning."** The threat model has 8 mitigations and a panic passphrase — don't undermine it with trivially reversible encoding.
- Core decrypts → injects as env var → adapter reads `os.environ` → dies with process
- Adapter NEVER touches encrypted files or master key
- `0o600` permissions on all credential files

### Rate Limiting (Anti-Abuse)

```json
"rate_limits": {
  "inbound_per_user_per_minute": 5,
  "inbound_per_user_per_hour": 30,
  "inbound_global_per_minute": 50,
  "outbound_replies_per_hour": 30,
  "outbound_posts_per_day": 10
}
```

Exceeding inbound limits → silently drop (no error to attacker). Outbound limits enforced before API call.

---

## CLI & Chat Interface

```bash
# Lifecycle
kognisant channel add <name> --platform <x|telegram|discord|reddit|webhook> --mode <assistant|manager|hybrid>
kognisant channel remove <name>
kognisant channel list
kognisant channel status [name]
kognisant channel start <name>
kognisant channel stop <name>
kognisant channel pause <name>
kognisant channel resume <name>
kognisant channel lockdown              # Emergency stop ALL

# Configuration
kognisant channel config <name>         # Open in $EDITOR
kognisant channel set-credentials <name>
kognisant channel set-owner <name> --id <platform_user_id>
kognisant channel set-persona <name> --voice "..."
kognisant channel templates <name>      # Edit template bank

# Monitoring
kognisant channel logs <name> [--follow]
kognisant channel metrics <name>        # Weekly report
kognisant channel escalations [--resolve <id>]
kognisant channel queue <name>          # Current queue depth + items

# Safety
kognisant channel test <name>           # Send test message
kognisant channel revoke-sessions <name>
```

Chat slash commands:
```
/channels              List with status
/channel status <name> Detailed view
/channel metrics <name>
/channel escalations
/approve_all           Approve all pending content
/reject <id>           Reject specific pending item
```

---

## File System Layout

```
~/.kognisant_core/
├── channels/
│   ├── channels.json             # Registry [{name, platform, mode, config...}]
│   ├── channels.lock             # Advisory lock (fcntl)
│   ├── credentials/              # Encrypted (0o600)
│   │   ├── telegram_my-bot.enc
│   │   └── x_brand-account.enc
│   ├── personas/                 # Reusable persona definitions
│   │   └── brand-voice.json
│   ├── templates/                # Per-channel template banks
│   │   ├── brand-x.json
│   │   └── support-telegram.json
│   ├── tos_rules/                # Platform TOS rule sets
│   │   └── x_2026-03.json
│   ├── escalations.jsonl         # Human review queue (append-only)
│   ├── sessions/                 # Per-channel state
│   │   └── brand-x/
│   │       └── state.db          # SQLite: threads, messages, metrics
│   └── logs/
│       └── brand-x.log
├── scripts/                      # Adapters live here
│   ├── channel_telegram.py
│   ├── channel_telegram.json
│   ├── channel_telegram_requirements.txt
│   ├── channel_telegram_venv/
│   ├── channel_x.py
│   ├── channel_x_venv/
│   └── ...
│
# Runtime (ephemeral):
/tmp/
├── kognisant_channel_brand-x.sock      # UDS
└── kognisant_cfg_brand-x.json          # Temp config (0o600)
```

---

## Implementation Phases

### Phase 1: Remote Assistant via Telegram — 4 weeks

**Goal**: Message Kognisant from Telegram, get full AI responses with tools.

- [ ] `channels.py`: `ChannelManager`, `ChannelRouter`, config schema
- [ ] UDS server: create/listen/accept per channel, `select.poll()` multiplexing
- [ ] Protocol v1.0: length-prefixed binary, `hello`/`hello_ack` handshake
- [ ] Bidirectional heartbeat (seq numbers, 3-miss detection)
- [ ] Config delivery via temp file + env var
- [ ] Credential encryption (tiered: `cryptography` → OS keyring → obfuscated)
- [ ] Core decrypts → env var injection at adapter spawn
- [ ] Reference Telegram adapter (`python-telegram-bot`)
- [ ] Adapter venv auto-setup (create venv, install requirements)
- [ ] Owner auth (sender_id matching)
- [ ] Integration with `chat.py` (inject message → full tools → return response)
- [ ] Response formatting for Telegram (markdown, 4096 char splitting)
- [ ] Register channel as persistent job
- [ ] Session auth (optional PIN, 8h timeout)
- [ ] Action confirmation (challenge-response for destructive ops)
- [ ] CLI: `channel add/remove/list/start/stop/set-credentials/lockdown`
- [ ] Chat: `/channels`, `/channel status`
- [ ] Audit logging

**Phase 1 build order** (dependency chain):
1. UDS server + protocol v1.0 (foundation — everything depends on this)
2. ChannelManager + config schema (CRUD, locking, validation)
3. Credential encryption (hard failure without `cryptography` or OS keyring)
4. Reference Telegram adapter (venv setup, env var injection)
5. Owner auth + chat.py injection (the core value prop)
6. Session tokens + action confirmation (security layers 2-3)
7. Audit logging + `/lockdown` (security layers 5-6)
8. CLI commands + response formatting (UX polish)

### Phase 2a: SMM Foundation — 6 weeks

**Goal**: Content calendar + template auto-responder on X/Twitter.

- [ ] `channel_moderation.py` (opt-in, only loaded when mode=manager/hybrid)
- [ ] `manager_respond()` (zero tools, separate from chat.py)
- [ ] `ResponseQueue` with P0-P4 priorities + deadline dropping + preempt-at-enqueue semantics
- [ ] `TemplateBank` (frequency-ordered matching, regex + keyword, ReDoS validation on add)
- [ ] `ThreadStateDB` (SQLite with WAL mode, thread-safety documented)
- [ ] `CostGate` (budget tracking, template_only fallback with 1-hour hysteresis)
- [ ] Content calendar (cron-triggered, P0 priority, topics from file)
- [ ] Content generation pipeline (read docs/commits → LLM → validate → post)
- [ ] Reference X/Twitter adapter (`tweepy`)
- [ ] Active hours / timezone support (P2-P4 soft filter; P0/P1 ignore; in-flight jobs continue to deadline)
- [ ] Approval queue mode (opt-in strict: require owner approval before posting)
- [ ] Weekly metrics report (`channel metrics`)
- [ ] Conservative defaults: template-only first 2 weeks, LLM opt-in

### Phase 2b: SMM Intelligence — 6 weeks

**Goal**: Auto-learning templates, competitive tracking, full moderation.

- [ ] Auto-template discovery (3+ similar responses → batched weekly suggestion, not per-prompt)
- [ ] Competitive mention tracking (sentiment-gated: neutral/positive → respond, negative → escalate)
- [ ] Full moderation pipeline: spam filter → TOS → intent → score → decision
- [ ] Escalation system (notify owner via channel, queue for review)
- [ ] Output validation: private data scanning, canary detection
- [ ] Platform TOS rule engine (pluggable rule JSONs)
- [ ] `channel escalations [--resolve]` command
- [ ] `channel templates` command (edit in $EDITOR)
- [ ] Per-user inbound rate limiting
- [ ] Thread resolution tracking (active → resolved → abandoned)
- [ ] Insights generation (FAQ gaps, feature request detection)

### Phase 2c: SMM Optimization — 4 weeks

**Goal**: A/B testing, cross-platform posting, learning loop.

- [ ] Cross-platform content adaptation (one topic → per-platform LLM call with tuned prompts)
- [ ] A/B prompt testing (two persona variants, track which gets more engagement)
- [ ] Response quality feedback (owner 👍/👎 → weight adjustment)
- [ ] Auto-template generation from repeated LLM responses
- [ ] Graduated response degradation (frequent user → template → mute)
- [ ] Slow-mode for new interactions (30-60s delay)

### Phase 3: Multi-Platform + Remote PERP — 6 weeks

**Goal**: Discord, Reddit adapters. Agent swarms from any channel.

- [ ] Reference Discord adapter (`discord.py`)
- [ ] Reference Reddit adapter (`praw`)
- [ ] Remote PERP: `/agent <task>` from any channel → swarm on local machine
- [ ] Progress updates pushed to channel during swarm
- [ ] Cross-channel session continuity (same memory regardless of channel)
- [ ] Agent-generated adapters for niche platforms (Mastodon, Bluesky, etc.)
- [ ] Anomaly detection (unusual access patterns → force re-auth)

### Phase 4: Growth & Analytics — ongoing

**Goal**: Platform API analytics, community health, ecosystem.

- [ ] Platform API analytics (paid tier: impressions, clicks, follower growth)
- [ ] Sentiment analysis on inbound mentions
- [ ] Optimal posting time detection (from engagement patterns)
- [ ] Content performance tracking (which posts drive mentions)
- [ ] Community health scoring (reply rate trend, sentiment trend)
- [ ] WhatsApp adapter (Business API)
- [ ] Generic webhook adapter (HMAC-verified)
- [ ] Multi-owner/team support
- [ ] Adapter marketplace (community-contributed scripts)

---

## Dependencies & Constraints

### Core (stdlib-only, Requirement 13)

- `socket` (AF_UNIX) + `select.poll()` — IPC multiplexing (no fd limit issues on macOS)
- `struct` — wire format (length-prefixed binary)
- `sqlite3` — thread state, metrics, template usage (WAL mode)
- `json` + `fcntl` — config, locking
- `subprocess`, `os`, `signal` — process management
- `re` — template pattern matching (with ReDoS validation)
- `cryptography` (**required** for credential encryption — hard failure if missing, no obfuscation fallback)

### Adapter Scripts (unrestricted)

Each adapter uses proper maintained libraries in isolated virtualenvs:
- `python-telegram-bot`, `tweepy`, `discord.py`, `praw`, etc.
- Core stays zero-dependency regardless

### Open Questions → Decisions

1. **Venv strategy**: **Per-adapter venvs** (default). Disk is cheap, dependency conflicts are expensive. Add `--shared-venv` flag later for power users who know their adapters don't conflict.

2. **Multi-device**: **1:1 binding** (one channel per daemon). For multi-device access, use assistant mode on multiple channels (e.g., Telegram on laptop, Discord on server). Shared/relay mode is Phase 4 complexity.

3. **Group chats**:
   - Assistant mode (groups): **@mention only**, always.
   - Manager mode (groups/servers): Configurable. Default: "reply to all messages in designated channels, @mention only elsewhere."
   ```json
   "group_behavior": {
     "designated_channels": ["#support", "#general"],
     "reply_mode": "all_in_designated",
     "ignore_channels": ["#off-topic", "#random"]
   }
   ```

4. **Content sources**: Pluggable interface. Phase 2a ships with `file` (content-plan.md) and `git` (commits/changelog). Phase 2b adds `rss` and `github_discussions`.
   ```python
   class ContentSource(ABC):
       @abstractmethod
       def fetch_topics(self) -> list[str]: ...
       @abstractmethod
       def fetch_activity(self) -> list[str]: ...
   ```

5. **WhatsApp**: Business API (official) for Phase 4. Not worth the unofficial bridge risk.

6. **Adapter marketplace**: Phase 4. Community-contributed scripts in a public repo, pull with `kognisant channel install-adapter <name>`.

---

## Relation to Existing Systems

| Existing Feature | How Channels Uses It |
|------------------|---------------------|
| `chat.py` pipeline | Owner messages (assistant mode) inject into full tool pipeline |
| `manager_respond()` (NEW) | Public messages → zero-tool text generation |
| `ResponseQueue` (NEW) | Priority queue with deadlines, templates, cost gate |
| `ThreadStateDB` (NEW) | SQLite for conversations, metrics, template tracking |
| `ContentCalendar` (NEW) | Cron-triggered post generation, enters queue at P0 |
| Persistent jobs | Each channel = 1 persistent job |
| `ProcessManager` | Spawns/kills adapters (via venv python) |
| `CronParser` | Content scheduling |
| Job queue locking | Concurrent channel config safety |
| Scripts system | Adapters = scripts (same storage, metadata, venv) |
| Project memory | Owner assistant has full access; manager gets NONE |
| Fast path classifier | Message complexity classification |
| Self-model engine | Track best-performing models per platform |
| Telemetry | Token usage per channel per mode |

### Manager Mode Is Opt-In

`channel_moderation.py` only imported when `mode: "manager"` or `"hybrid"`. Assistant-only channels never load SMM code. Zero overhead for users who only want remote access.

---

## Example Flows

### Flow 1: Remote coding from phone

```
[Telegram DM to @my_kognisant_bot]

You: what's failing in the tests?

Kognisant: 2 failures in test_token_expiry.py:
  - test_refresh_expired: timezone-naive comparison on line 42
  - test_validate_stale: off-by-one in TTL check

You: /agent fix both

Kognisant: 🐝 PERP Swarm Activated (2 subtasks)
  ✅ Agent [1]: Fixed timezone comparison
  ✅ Agent [2]: Fixed TTL off-by-one
✨ Done. All 14 tests passing.

You: commit it

Kognisant: ✅ Committed: "fix: timezone + TTL in token expiry tests" (abc1234)
```

### Flow 2: SMM in action (daily cycle)

```
[9:00 AM — Content calendar fires]
Bot generates: "🔥 Kognisant tip: Your AI remembers everything.
Load context once → every future session knows your architecture.
No more re-explaining. pip install kognisant #devtools"
→ Posted to X ✅

[Throughout the day — Mentions arrive]
@user1: "how do I install this?"
→ Template hit: "Hey! pip install kognisant — guide at docs.kognisant.dev 🔧"
→ Instant reply ✅ (zero LLM cost)

@user2: "Does this work with Claude API?"
→ No template match → queued for LLM (P2, deadline: 2h)
→ LLM generates: "Yes! Add your Claude API key in the model pool config.
   Docs: kognisant.dev/docs/models-and-providers"
→ Reply sent (45 min later) ✅

@user3: "this project is garbage, you stole my code"
→ Moderation score: 0.8 (above threshold)
→ Escalated to owner ⚠️
→ Owner notified: "Escalation from @user3 — review needed"
```

### Flow 3: Weekly report

```
$ kognisant channel metrics brand-x

📊 Weekly Report: brand-x
─────────────────────────
Posts: 21 | Threads: 3
Mentions: 47 | Replies: 41 (87%)
  Templates: 28 (68%) | LLM: 13 (32%)
Avg Reply Time: 1.4h
Dropped: 4 | Escalations: 2
LLM Budget: 38/350 used (11%)

📌 Insights:
  "How to install?" → 8x (template exists, working well)
  "Windows support?" → 5x (no template — ADD ONE or update docs)
  "vs Aider?" → 3x (competitive — consider comparison page)
```

---

## The Value Proposition

**For the $0/month price of local compute:**

- ✅ Consistent daily content (3x/day if configured)
- ✅ Community replies within active hours (template: instant, LLM: <2h)
- ✅ Moderation and crisis escalation
- ✅ Brand voice consistency (persona-locked)
- ✅ Actionable insights (FAQ gaps, feature demand signals)
- ✅ Remote AI assistant (full project access from any device)
- ✅ Zero data leaves your machine
- ✅ Works while you sleep

**What it replaces:** The $150/month Fiverr SMM gig, the "I'll get to Twitter later" guilt, the community messages that go unanswered for days.

**What it doesn't replace (yet):** Platform-native analytics (paid API tiers), visual content (images/video), influencer outreach, paid ad management.

**Communicate the queue as a feature:** "Kognisant processes your community engagement in batches, just like a human social media manager. Expect replies within active hours — instant for common questions (templates), 1-2 hours for novel ones (LLM)."
