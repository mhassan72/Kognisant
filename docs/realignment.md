# Kognisant CLI — Runtime Realignment Plan

> Comprehensive redesign of the chat execution pipeline to implement a 5-phase
> cognitive lifecycle: **Bootstrap → Plan → Execute → Reflect → Persist**.
>
> Inspired by the Kognisant Cloud runtime. Adapted for single-user, local-first,
> zero-billing, zero-infrastructure CLI operation.

---

## Table of Contents

- [Problem Statement](#problem-statement)
- [Architecture Overview](#architecture-overview)
- [Execution Flow Diagram](#execution-flow-diagram)
- [Phase 1 — Bootstrap](#phase-1--bootstrap)
- [Critique & Refinements](#critique--refinements-5-iterations)
- [Phase 2 — Plan](#phase-2--plan)
- [Phase 3 — Execute](#phase-3--execute)
- [Phase 4 — Reflect](#phase-4--reflect)
- [Phase 5 — Persist](#phase-5--persist)
- [SelfModel Engine](#selfmodel-engine)
- [FastPath Classifier](#fastpath-classifier)
- [Circuit Breaker](#circuit-breaker)
- [User Experience — What the User Sees](#user-experience--what-the-user-sees)
- [File Structure](#file-structure)
- [Data Schemas](#data-schemas)
- [Integration Points](#integration-points)
- [Implementation Order](#implementation-order)
- [What We Skip (and Why)](#what-we-skip-and-why)
- [Design Principles](#design-principles)

---

## Problem Statement

### Current State

```
User types "hello"
  → Build 10,000-token payload (15 skills, 29 tools, context.md, file listing)
  → Send entire payload to remote 550B-parameter model
  → Wait 3-5 minutes (black screen with opaque spinner)
  → Either: response arrives, timeout, empty response, or crash on Ctrl+C
```

**Failures observed:**
1. No communication to user during wait — "paranoia-inducing black box"
2. No classification — "hello" gets same payload as "refactor the auth module"
3. No learning — same timeout repeats forever, no model fallback
4. No graceful cancellation — Ctrl+C crashes with full traceback
5. No circuit breaking — dead endpoints are hammered indefinitely
6. Empty responses displayed as blank lines with no explanation

### Target State

```
User types "hello"
  → Bootstrap: load SelfModel, check health, select model (20ms)
  → Plan: classify SIMPLE, build 200-token payload (1ms)
  → Execute: call fastest reliable model, stream tokens (2-5s)
  → Reflect: record success, update valence (5ms)
  → Persist: write updated SelfModel (10ms)
  → User sees every phase happening in real-time
```

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         chat.py (outer loop)                        │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  Slash commands (/help, /agent, /spec, /model, /jobs, etc.)   │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              │ (non-slash messages)                  │
│                              ▼                                       │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                      runtime.execute()                         │  │
│  │  ┌─────────┐ ┌──────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐   │  │
│  │  │BOOTSTRAP│→│ PLAN │→│ EXECUTE │→│ REFLECT │→│ PERSIST │   │  │
│  │  └─────────┘ └──────┘ └─────────┘ └─────────┘ └─────────┘   │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              │                                       │
│                              ▼                                       │
│                    ExecutionResult returned                          │
│                              │                                       │
│                              ▼                                       │
│              Display response (if not already streamed)              │
└─────────────────────────────────────────────────────────────────────┘
```

### Module Dependency Graph

```
runtime.py
  ├── fast_path_classifier.py    (Plan phase)
  ├── self_model_engine.py       (Bootstrap + model selection + circuit breaker)
  ├── reflect_engine.py          (Reflect phase)
  ├── network.py                 (Execute phase — LLM calls)
  ├── tools.py                   (Execute phase — tool execution)
  └── config.py                  (model pool, project info)
```

---

## Execution Flow Diagram

### Happy Path (SIMPLE message — "hello")

```
User input: "hello"
     │
     ▼
┌─ BOOTSTRAP (20ms) ──────────────────────────────────┐
│  1. Load self_model.json                             │
│  2. Decay valence (10%/day toward 0)                 │
│  3. Check circuit breaker for configured model       │
│  4. Select model (configured, or fallback if broken) │
│  5. Print: "⚡ GPT OSS 120b ready (valence: +22)"   │
└──────────────────────────────────────────────────────┘
     │
     ▼
┌─ PLAN (1ms) ─────────────────────────────────────────┐
│  1. FastPathClassifier: word_count=1, no action verbs │
│     → SIMPLE                                          │
│  2. Build minimal system prompt (2 sentences)         │
│  3. No tools attached                                 │
│  4. History: last 2 messages only                     │
│  5. Print: "📋 SIMPLE → direct response"             │
└───────────────────────────────────────────────────────┘
     │
     ▼
┌─ EXECUTE ────────────────────────────────────────────┐
│  1. Print: "⚙️  GPT OSS 120b — 0s"                  │
│  2. Stream tokens to stdout as they arrive           │
│  3. Update elapsed timer on spinner                  │
│  4. On Ctrl+C → clean rollback, stay in chat         │
│  5. On empty response → show explicit message        │
│  6. On timeout → record failure, show message        │
└───────────────────────────────────────────────────────┘
     │
     ▼
┌─ REFLECT HOT (5ms) ──────────────────────────────────┐
│  1. Record: success=true, time=2.1s, model=gpt-oss   │
│  2. Valence: +5 (success + fast)                     │
│  3. Model reliability Bayesian update: +1 success    │
│  4. Print: "🔍 2.1s | valence: +27 (+5)"            │
└───────────────────────────────────────────────────────┘
     │
     ▼
┌─ PERSIST (10ms) ─────────────────────────────────────┐
│  1. Atomic write self_model.json                     │
│  2. Increment total_executions                       │
└───────────────────────────────────────────────────────┘
```

### Happy Path (CONTEXT message — "what are we working on?")

```
User input: "what are we working on?"
     │
     ▼
┌─ BOOTSTRAP (20ms) ───────────────────────────────────┐
│  Same as above — load state, select model            │
└───────────────────────────────────────────────────────┘
     │
     ▼
┌─ PLAN (1ms) ─────────────────────────────────────────┐
│  1. FastPathClassifier: 6 words, "working on" = proj │
│     reference, no action verbs → CONTEXT             │
│  2. Build context system prompt:                     │
│     - Agent identity (brief)                         │
│     - Full context.md                                │
│     - Project name + file listing                    │
│  3. No tools attached                                │
│  4. History: last 10 messages                        │
│  5. Print: "📋 CONTEXT → project memory, no tools"  │
└───────────────────────────────────────────────────────┘
     │
     ▼
┌─ EXECUTE ────────────────────────────────────────────┐
│  Stream response from model                          │
│  (Model uses context.md to answer the question)      │
└───────────────────────────────────────────────────────┘
     │
     ▼
  REFLECT + PERSIST (same as SIMPLE)
```

### Happy Path (COMPLEX message — "fix the bug in auth.py")

```
User input: "fix the bug in auth.py"
     │
     ▼
┌─ BOOTSTRAP (20ms) ───────────────────────────────────┐
│  Same — load state, select model                     │
└───────────────────────────────────────────────────────┘
     │
     ▼
┌─ PLAN (1ms) ─────────────────────────────────────────┐
│  1. FastPathClassifier: "fix" = action verb,         │
│     "auth.py" = file reference → COMPLEX             │
│  2. Build full system prompt:                        │
│     - Agent identity                                 │
│     - context.md + memory guidelines                 │
│     - Project file listing                           │
│     - Skill names                                    │
│  3. Attach full tool set (29 tools)                  │
│  4. History: last 20 messages (pruned)               │
│  5. Print: "📋 COMPLEX → full pipeline, tools on"   │
└───────────────────────────────────────────────────────┘
     │
     ▼
┌─ EXECUTE (with tool loop) ───────────────────────────┐
│  1. Call LLM with tools                              │
│  2. If tool_calls returned:                          │
│     a. Print PLAN section (tool descriptions)        │
│     b. Execute tools, print EXECUTION results        │
│     c. Append tool results to messages               │
│     d. Loop back to step 1 (max 3 rounds)            │
│  3. Final response streamed to user                  │
└───────────────────────────────────────────────────────┘
     │
     ▼
┌─ REFLECT HOT (5ms) ──────────────────────────────────┐
│  1. Valence update                                   │
│  2. Model reliability update                         │
│  3. Tool reliability update (for each tool used)     │
│  4. Print summary                                    │
└───────────────────────────────────────────────────────┘
     │
     ▼
  PERSIST
```

### Failure Path (Timeout + Auto-Fallback)

```
User input: "hello"
     │
     ▼
  BOOTSTRAP → selects Nemotron-550B (configured default)
     │
     ▼
  PLAN → SIMPLE
     │
     ▼
┌─ EXECUTE ────────────────────────────────────────────┐
│  1. Spinner: "⚙️  Nemotron-550B (NVidia) — 0s"      │
│  2. Timer counts: 10s... 20s... 30s (SIMPLE timeout) │
│  3. TIMEOUT at 30s                                   │
│  4. Print: "⚠️  Timeout after 30s"                   │
└───────────────────────────────────────────────────────┘
     │
     ▼
┌─ REFLECT HOT ─────────────────────────────────────────┐
│  1. Valence: -15 (timeout)                            │
│  2. Model reliability: +1 failure for Nemotron-550B   │
│  3. Circuit breaker: failures++ (now 1/5 threshold)   │
│  4. Print: "🔍 Timeout | valence: +7 (-15)"          │
│  5. Print: "   Nemotron-550B reliability: 0.40 (2/5)" │
└───────────────────────────────────────────────────────┘
     │
     ▼
  PERSIST (write updated state)
     │
     ▼
  User tries again → same timeout → reliability drops to 0.25
     │
     ▼
  3rd attempt:
┌─ BOOTSTRAP ───────────────────────────────────────────┐
│  Nemotron-550B reliability: 0.25 (<0.3 threshold)     │
│  Alternative: GPT OSS 120b reliability: 0.90          │
│  AUTO-SWITCH                                          │
│  Print: "⚡ Switching to GPT OSS 120b (GROQ)"        │
│  Print: "   Nemotron-550B unreliable (1/4 success)"   │
└───────────────────────────────────────────────────────┘
     │
     ▼
  PLAN → SIMPLE → EXECUTE with GPT OSS 120b → 2s response → success
```

### Failure Path (Circuit Breaker Opens)

```
5 failures from same model within 30 seconds
     │
     ▼
┌─ BOOTSTRAP ───────────────────────────────────────────┐
│  Circuit breaker: Nemotron-550B = OPEN                │
│  Don't even attempt the call                          │
│  Find alternative model immediately                   │
│  Print: "⚡ Nemotron-550B circuit OPEN (5 failures)"  │
│  Print: "⚡ Using GPT OSS 120b instead"               │
└───────────────────────────────────────────────────────┘
```

### Failure Path (Ctrl+C Cancellation)

```
┌─ EXECUTE ─────────────────────────────────────────────┐
│  Spinner: "⚙️  Nemotron-550B (NVidia) — 45s"         │
│  User presses Ctrl+C                                  │
└───────────────────────────────────────────────────────┘
     │
     ▼
┌─ INTERRUPT HANDLER ───────────────────────────────────┐
│  1. Stop spinner immediately                          │
│  2. Rollback messages to checkpoint                   │
│  3. Record as "cancelled" (valence: -5)               │
│  4. Print: "Cancelled. Model was taking too long."    │
│  5. Print: "Tip: /model to switch"                    │
│  6. Return to prompt — chat stays alive               │
└───────────────────────────────────────────────────────┘
```

---

## Phase 1 — Bootstrap

**Purpose:** Load all cognitive state, inventory local capabilities, check system health, select the best model.
Runs every message. Pure local computation. No network calls.

### Steps

| # | Action | Detail |
|---|--------|--------|
| 1 | Load SelfModel | Read `~/.kognisant_core/self_model.json`. If missing, use safe defaults. |
| 2 | Inventory capabilities | Scan what exists locally: scripts, skills, tools, jobs, registered projects. |
| 3 | Load project context | Read `.kognisant/context.md`, `memory-guidlines.md`, check world model state. |
| 4 | Temporal decay | Valence decays 10% toward 0 per day. Frustration halves every 24h. |
| 5 | Circuit breaker check | For each model: if `state == "open"` and cooldown expired → `"half_open"`. |
| 6 | Model selection | Pick model: configured default if healthy, else highest-reliability alternative. |
| 7 | Health status print | Show user: model name, valence, capability summary, any auto-switch explanation. |

### Capability Inventory (what Bootstrap sees)

Bootstrap scans the local system to build a snapshot of what the agent has available.
This snapshot informs the Plan phase (what tools/skills are relevant) and the
system prompt (what the model should know about its own capabilities).

```
~/.kognisant_core/
  ├── scripts/          → count .py files → "N scripts available"
  ├── skills/           → count .md files → "N skills loaded"
  ├── tools/            → count .json files → "N custom tools registered"
  ├── jobs.json         → count non-terminal jobs → "N active jobs"
  ├── projects.json     → list registered projects
  ├── models_pool.json  → compiled model list with provider status
  └── self_model.json   → valence, reliability, frustration

<project>/.kognisant/
  ├── config.json       → project name, world_model_enabled flag
  ├── context.md        → build memory (tasks tracked, phase)
  ├── memory-guidlines.md → steering rules
  ├── world_model/      → IF enabled: node/edge counts from index.json
  ├── specs/            → count spec dirs, their statuses
  ├── goals/learning.json → active goal count
  └── history/          → session count (for experience level)
```

### Bootstrap Output Structure

```python
@dataclass
class BootstrapState:
    # SelfModel state
    valence: int
    frustration: float
    total_executions: int
    consecutive_failures: int
    
    # Selected model
    active_model: dict          # The model config to use
    auto_switched: bool         # True if different from configured default
    switch_reason: str          # Why (empty if not switched)
    
    # Capability inventory
    scripts_count: int          # ~/.kognisant_core/scripts/*.py
    skills_count: int           # ~/.kognisant_core/skills/*.md
    skills_names: list[str]     # Skill file names (without .md)
    tools_count: int            # ~/.kognisant_core/tools/*.json (custom tools)
    active_jobs_count: int      # Jobs in non-terminal state
    registered_projects: int    # Total registered projects
    
    # Project-specific context
    project_name: str
    context_md_loaded: bool     # True if context.md exists and was read
    context_md_content: str     # The actual content (for system prompt)
    guidelines_loaded: bool     # True if memory-guidlines.md exists
    guidelines_content: str     # The actual content
    world_model_enabled: bool   # From config.json
    world_model_node_count: int # 0 if disabled or empty
    active_specs: list[dict]    # [{name, status, progress}]
    active_goals_count: int     # From goals/learning.json
    session_history_count: int  # Number of past sessions (experience)
    
    # Model pool health
    models_available: list[dict]  # All compiled models with reliability scores
    circuit_breakers: dict        # Current breaker states
```

### Valence Inputs (comprehensive)

Valence isn't just about "did the last LLM call work." It reflects the total health
of the system — tools, scripts, jobs, learning state:

```python
# Valence is influenced by:

# 1. LLM interaction success/failure (primary driver — ±5 to ±15 per execution)
# 2. Tool reliability (background signal):
#    - If avg tool reliability across all tools drops below 0.5 → valence pressure -2/execution
#    - If all tools above 0.8 → no pressure
# 3. Job health (background signal):
#    - Active jobs in "failed" or "crash_loop" → valence pressure -1/execution per failed job
#    - All jobs healthy → no pressure
# 4. World model freshness (if enabled):
#    - change_log.json last_commit matches current HEAD → fresh (no pressure)
#    - Stale (>24h since last update) → valence pressure -1/execution
# 5. Goal completion rate (background signal):
#    - From LearningLoop acceptance_rate: if < 0.3 → valence pressure -1/execution
#    - This means the system's suggestions are mostly being rejected

# These background pressures accumulate slowly and signal systemic issues.
# They never dominate (max -5/execution from all background signals combined).
# But over 20+ executions of systemic problems, they drag valence down enough
# to trigger conservative behaviour.
```

### Model Selection Logic

```
IF configured_model circuit_breaker.state == "closed":
    USE configured_model
ELIF configured_model circuit_breaker.state == "half_open":
    USE configured_model (one test attempt)
ELIF alternative exists with reliability > 0.5:
    AUTO-SWITCH to best alternative
    NOTIFY user with reason
ELSE:
    USE configured_model anyway (no better option)
    WARN user: "This model has been unreliable"
```

### Auto-Switch Threshold

A model becomes "unreliable" when:
- `reliability < 0.3` AND `attempts >= 3`

This prevents auto-switching on a single bad request. You need evidence.

---

## Critique & Refinements (5 iterations)

The following refinements address gaps found through systematic review of the plan
against the core requirement: **the user must never be left in the dark.**

### Refinement 1: First-Run Experience

On the very first execution, `self_model.json` doesn't exist. The Bootstrap phase
must handle this gracefully and communicate clearly:

```
FIRST RUN:
  ⚡ Welcome — first execution. Using Nemotron-550B (configured default). No history yet.

SUBSEQUENT RUNS:
  ⚡ GPT OSS 120b | valence: +22 | 15 skills, 6 tools, 0 jobs active
```

**Rule:** The ⚡ line MUST appear within 50ms of the user pressing Enter.
Bootstrap loads `self_model.json` first (fast), prints ⚡ immediately, THEN
scans capabilities. The capability count appears in the ⚡ line only if Bootstrap
can scan it in <50ms total. If disk is slow, print ⚡ without counts and append
them on the 📋 line instead.

### Refinement 2: Connection vs Thinking Feedback

The ⚙️ spinner must distinguish between "waiting for server to accept connection"
and "server connected, waiting for model to generate":

```
⚙️  GPT OSS 120b — connecting... 1s        ← HTTP request in flight
⚙️  GPT OSS 120b — thinking... 5s          ← Headers received, waiting for first SSE chunk
⚙️  GPT OSS 120b — streaming... 8s         ← First token arrived, actively receiving
```

Implementation: `urllib.request.urlopen()` blocks until headers arrive. Once it returns
(response object exists), switch from "connecting" to "thinking". Once first `data:` line
is parsed, switch to "streaming" (and start printing tokens).

This tells the user:
- "connecting" = network/DNS/TLS handshake in progress
- "thinking" = server accepted request, model is processing
- "streaming" = tokens flowing (response will appear)

If stuck on "connecting" for >10s, it's likely a network issue.
If stuck on "thinking" for >60s, it's likely a model overload issue.

**Network.py change required:** `query_model_api_stream()` must yield a new event type
`("phase", "connected")` after `urlopen()` returns but before parsing SSE lines.
The runtime uses this to update the spinner text.

```python
# In query_model_api_stream():
response = urllib.request.urlopen(req, timeout=timeout, context=context)
yield ("phase", "connected")  # <-- NEW: signals connection established

# Then existing SSE parsing loop...
for raw_line in response:
    ...
    yield ("content", delta["content"])  # existing
```

### Refinement 3: All Models Down

When all configured models have circuit breakers in OPEN state:

```
⚡ ⚠️  All models unreliable
  • Nemotron-550B: OPEN (5 failures, cooldown 20s remaining)
  • GPT OSS 120b: OPEN (5 failures, cooldown 8s remaining)
  • Kimi-K2.6: OPEN (3 failures, cooldown 25s remaining)
  
  Waiting for GPT OSS 120b cooldown (8s)...
  Or /model to add a new provider.
```

The runtime waits for the shortest cooldown to expire, then attempts one half_open
test with that model. If it succeeds → proceed. If it fails → try next shortest.

This prevents the user from being stuck with "no model available, do nothing" —
the system actively recovers by waiting the minimum necessary time.

Maximum wait: 30s (the circuit breaker cooldown period). If all cooldowns are >30s,
just try the one that failed longest ago.

### Refinement 4: History Window and Context Continuity

The sliding window (SIMPLE: 2, CONTEXT: 10, COMPLEX: 20) can lose context from
earlier turns. This is by design — to prevent token overflow — but must be documented:

**Rule:** The LAST assistant response is ALWAYS included regardless of window size.
This ensures continuity — the model always knows what it just said, even in SIMPLE mode.

Updated history rules:
```
SIMPLE:  system_prompt + [last assistant message] + user message
CONTEXT: system_prompt + last 10 messages (includes assistant responses)
COMPLEX: system_prompt + last 20 messages (pruned, tool results compressed)
```

If the user says "thanks" (SIMPLE), the model sees its own last response and can
say something contextual like "You're welcome! Let me know if the auth fix works."
instead of a generic "You're welcome!"

### Refinement 5: Fast Tool — No Animation Flicker

Tools that complete in <150ms (most file operations) should NOT show the animated
spinner. The animation would flicker for 1 frame then disappear — ugly and distracting.

**Rule:** Tool execution is synchronous on the main thread. Animation is conditional:

```python
start = time.monotonic()
result = execute_tool(name, args, project_info)
duration = time.monotonic() - start

if duration < 0.15:
    # Fast tool — print completed box directly (no animation, no flicker)
    _print_static_tool_box(tool_name, args, success, duration, summary)
else:
    # Slow tool — animation was running, finalize it
    # (animation thread started preemptively, joined here)
    _finalize_animated_tool_box(success, duration, summary)
```

**Revised approach for all tools:**
1. Print the top border + progress line immediately (static, not animated)
2. Execute the tool synchronously
3. If duration < 150ms: overwrite progress line with result (no animation was visible)
4. If duration ≥ 150ms: the progress line was already pulsing (animation thread was running), finalize it

This means for fast tools the user sees:
```
  ┌─ Reading auth.py ───────────────────────────────────────────────────┐
  ┌─ Read auth.py ──────────────────────────────────────────────────────┐
  │ ✓ 0.01s | 2.8KB read                                               │
  └─────────────────────────────────────────────────────────────────────┘
```
The first line (in-progress header) is immediately overwritten by the completed header.
No flicker because it happens within one terminal refresh cycle (<16ms).

For slow tools (web search, shell commands) the user sees the full animation:
```
  ┌─ Searching 'flask patterns' ────────────────────────────────────────┐
  │ ◐ searching...      ← (pulsing gray↔orange for 2+ seconds)
  ┌─ Found results for 'flask patterns' ───────────────────────────────┐
  │ ✓ 2.3s | 8 results                                                 │
  └─────────────────────────────────────────────────────────────────────┘
```

### Refinement 6: Stream Stall Detection

The plan specifies timeouts per classification (SIMPLE=30s, CONTEXT=60s, COMPLEX=120s).
But these only cover the initial connection. Once streaming starts, the socket is "active"
and the timeout no longer applies. A model could send one token then stall forever.

**Rule: Stall timeout = 30s of no data received mid-stream.**

```python
STALL_TIMEOUT = 30  # seconds with no new data after streaming started

last_data_at = time.monotonic()

for raw_line in response:
    last_data_at = time.monotonic()
    # ... process line ...

# In a separate watchdog thread (or checked between line reads):
if time.monotonic() - last_data_at > STALL_TIMEOUT:
    response.close()
    raise KognisantAPIError("Stream stalled — no data for 30s")
```

The stall timeout is separate from the connection timeout:
- Connection timeout: SIMPLE=30s, CONTEXT=60s, COMPLEX=120s (passed to `urlopen()`)
- Stall timeout: always 30s (no data received after stream started)

User sees:
```
⚙️  Nemotron-550B — streaming... 45s
⚠️  Stream stalled — no data received for 30s. Connection dropped.
```

### Refinement 7: Unexpected Tool Calls in CONTEXT Mode

CONTEXT mode sends NO tool schemas. A well-behaved model cannot return tool_calls
without schemas. However, some models may include tool_call-like content in their
response if conversation history contains prior tool interactions.

**Rule:** If the streaming parser encounters `tool_calls` in a response when no
tools were sent, treat it as a normal content response. Ignore the tool_calls field.
Log it in telemetry as `unexpected_tool_calls: true`.

```python
# In Execute phase:
if tool_calls and ctx.classification in ("SIMPLE", "CONTEXT"):
    # Model hallucinated tool calls without schemas — ignore them
    tool_calls = None
    ctx.telemetry["unexpected_tool_calls"] = True
```

This prevents the runtime from entering the tool loop when no tools were authorized.

### Refinement 8: Checkpoint/Rollback Contract

The boundary between `chat.py` and `runtime.execute()` must be precisely defined:

```python
# chat.py does:
checkpoint_idx = len(messages)       # Save state BEFORE runtime
result = runtime.execute(...)        # Runtime may mutate messages
if not result.success:
    # Rollback: runtime already rolled back messages internally
    pass

# runtime.execute() does internally:
messages.append({"role": "user", "content": user_message})   # Step 1
save_chat_session(...)                                         # Step 2

try:
    # ... Execute LLM call, append assistant_message, tool results ...
except (KeyboardInterrupt, Exception):
    # Rollback: remove everything we added since checkpoint
    while len(messages) > checkpoint_idx:
        messages.pop()
    save_chat_session(...)   # Persist the rollback
```

**Contract:** The runtime is responsible for ALL mutations to `messages[]` and ALL
rollbacks. The chat.py caller never touches messages during execution. On return,
`messages[]` is in a consistent state — either the full turn was applied (success)
or it was rolled back to `checkpoint_idx` (failure/cancel).

### Refinement 9: Token Estimate Calibration

The Plan phase estimates tokens using `len(text) // 4`. Some API providers return
actual token counts in their response (`usage.prompt_tokens`, `usage.completion_tokens`).

**Rule:** If the API response includes `usage` data, compare actual vs estimated.
Store the ratio in SelfModel per model. Use it to refine future estimates.

```python
# In Reflect phase:
if api_response.get("usage"):
    actual_in = api_response["usage"]["prompt_tokens"]
    estimated_in = ctx.total_tokens_in
    ratio = actual_in / estimated_in if estimated_in > 0 else 1.0
    
    # Update model-specific calibration factor (rolling average)
    model_entry.token_calibration = (
        model_entry.token_calibration * 0.8 + ratio * 0.2
    )
```

Future estimates: `estimated_tokens = (len(text) // 4) * model.token_calibration`

This isn't critical for correctness — the system works fine with ±30% estimates.
But over time, the displayed token counts become more accurate per model.

### Refinement 10: Multi-Round Timeout Budget

In COMPLEX mode, the tool loop can run up to 3 rounds. Each round makes a separate
LLM call. The timeout must be per-round, not per-execution:

```
Round 1: LLM call (120s timeout) → returns tool_calls
         Tool execution (no timeout — tools have their own limits)
Round 2: LLM call (120s timeout) → returns tool_calls
         Tool execution
Round 3: LLM call (120s timeout) → returns final response
```

**Rule:** Each LLM call within the tool loop gets a fresh timeout. The total
execution can take up to `3 × 120s + tool_execution_time` in the worst case.

User sees the ⚙️ spinner reset for each round:
```
⚙️  GPT OSS 120b — connecting... 0s     (round 1)
  ┌─ Read auth.py ──────────────────────┐
  │ ✓ 0.01s | 2.8KB                    │
  └─────────────────────────────────────┘
⚙️  GPT OSS 120b — connecting... 0s     (round 2 — timer resets)
```

This prevents a scenario where round 1 takes 100s (leaving only 20s for round 2).

---

## Phase 2 — Plan
**Purpose:** Classify the message and build the minimum viable payload.
No LLM call. Rule-based classification.

### FastPath Classification

Three categories with strict boundaries:

```
┌─────────────────────────────────────────────────────┐
│                    User Message                      │
└─────────────┬───────────────────────────────────────┘
              │
              ▼
┌─────────────────────────┐
│  Word count ≤ 6?        │──── NO ────┐
│  No action verbs?       │            │
│  No file references?    │            │
│  No technical nouns     │            │
│  after question words?  │            │
└───────────┬─────────────┘            │
            │ YES                       │
            ▼                           ▼
      ┌──────────┐          ┌──────────────────────┐
      │  SIMPLE  │          │  Has action verbs?   │
      └──────────┘          │  Has file paths?     │
                            │  Code-like tokens?   │
                            │  Multi-sentence?     │
                            │  Word count > 30?    │
                            └──────────┬───────────┘
                                       │
                            YES to any? │ NO to all?
                            ┌───────┐  │  ┌─────────┐
                            │COMPLEX│  │  │ CONTEXT │
                            └───────┘  │  └─────────┘
                                       ▼
                                   CONTEXT
```

### Action Verbs (trigger COMPLEX)

```
fix, create, read, edit, write, modify, delete, remove, refactor,
implement, build, run, execute, test, deploy, add, update, install,
search, browse, download, schedule, script, migrate, optimize
```

### Project Reference Patterns (trigger CONTEXT)

```
"we", "our", "the project", "working on", "progress", "status",
"recap", "summary", "so far", "where were we", "what's next"
```

### File/Code Patterns (trigger COMPLEX)

```
Contains: .py, .js, .ts, .md, .json, .yaml, .toml
Contains: / or \ path separators
Contains: function_like_names (underscores + lowercase)
Contains: ClassName patterns (CamelCase)
```

### Payload Construction Per Category

| Category | System Prompt | Tools | History | Timeout |
|----------|--------------|-------|---------|---------|
| SIMPLE | Identity only (2 sentences) | None | Last assistant msg + user msg | 30s |
| CONTEXT | Identity + context.md + file listing | None | Last 10 messages | 60s |
| COMPLEX | Full (identity + context + guidelines + skills names) | All 29 | Last 20 (pruned) | 120s |

**History rule:** The last assistant response is ALWAYS included (even in SIMPLE) to
maintain conversational continuity. The model always knows what it just said.

### Token Budget Per Category

| Category | System Prompt | Tools | History | User Msg | Total |
|----------|--------------|-------|---------|----------|-------|
| SIMPLE | ~50 tokens | 0 | ~100 | variable | ~200 |
| CONTEXT | ~1,500 tokens | 0 | ~2,000 | variable | ~4,000 |
| COMPLEX | ~2,000 tokens | ~4,000 | ~4,000 | variable | ~10,000 |

---

## Phase 3 — Execute

**Purpose:** Call the LLM, handle streaming, manage tool loops, handle errors.

### Execution Modes

```
┌────────────────────────────────────────────────────────┐
│                    SIMPLE / CONTEXT                     │
│                                                        │
│  1. Single LLM call (no tools in payload)              │
│  2. Stream tokens directly to stdout                   │
│  3. No tool loop — response is final                   │
│  4. Timeout: 30s (SIMPLE) or 60s (CONTEXT)             │
└────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────┐
│                      COMPLEX                           │
│                                                        │
│  1. LLM call with full tool set                        │
│  2. If model returns tool_calls:                       │
│     a. Display PLAN (what tools will be called)        │
│     b. Execute each tool, display EXECUTION results    │
│     c. Append tool results to messages                 │
│     d. Call LLM again with updated context             │
│     e. Repeat up to 3 rounds                           │
│  3. Final response streamed to stdout                  │
│  4. Timeout: 120s per LLM call                         │
└────────────────────────────────────────────────────────┘
```

### Streaming Protocol

```
START:
  Stop previous spinner
  Print phase spinner: "⚙️  [model_name] — connecting... 0s"
  
HTTP RESPONSE HEADERS RECEIVED:
  Update spinner: "⚙️  [model_name] — thinking... Xs"
  
FIRST TOKEN ARRIVES:
  Stop phase spinner
  Print: "Kognisant >"
  Set _streamed = True
  Update spinner (brief): "⚙️  [model_name] — streaming..."
  
SUBSEQUENT TOKENS:
  Write directly to stdout (no buffering)
  
STREAM ENDS:
  Print newline
  Proceed to Reflect
  
TIMEOUT (no response within timeout window):
  Stop spinner
  Record as failure
  Print timeout message with phase info:
    "⚠️ Timeout after 30s (stuck on: connecting/thinking/streaming)"
  Proceed to Reflect
```

### Tool Execution Flow (COMPLEX only)

Each tool call is rendered as a self-contained box — no separate PLAN/EXECUTION/RESULT
headers. The box appears inline as the model requests tools, showing the function
signature at the top and the result inside:

```
Model returns tool_calls
     │
     ▼
┌─ For each tool_call: ─────────────────────────────────┐
│                                                        │
│  Print box top:                                        │
│    ┌─ tool_name('primary_arg_value') ──────────────┐   │
│                                                        │
│  Execute tool:                                         │
│    result = execute_tool(name, args, project_info)      │
│                                                        │
│  Print result inside box:                              │
│    │ ✓ 0.02s | 4.1KB read                         │   │
│    — or —                                              │
│    │ ✗ 2.1s | Connection timeout                   │   │
│                                                        │
│  Print box bottom:                                     │
│    └────────────────────────────────────────────────┘   │
│                                                        │
│  Append result to messages                             │
└────────────────────────────────────────────────────────┘
     │
     ▼
  Loop back to LLM call (round 2/3) or final response
```

### Box Anatomy

Each tool call has three visual states rendered inline with ANSI animation:

**State 1: IN PROGRESS (animated)**

```
  ┌─ Reading cli_kognisant/network.py ──────────────────────────────────┐
  │ ◐ reading...                                                        │
  └─────────────────────────────────────────────────────────────────────┘
```

The box border and text pulse between flat gray (`\033[38;2;149;165;166m`)
and flat orange (`\033[38;2;243;156;18m`) on a 300ms cycle while the tool
executes. The spinner character rotates: `◐ ◓ ◑ ◒`

**State 2: SUCCESS (final, static)**

```
  ┌─ Read cli_kognisant/network.py ─────────────────────────────────────┐
  │ ✓ 0.02s | 4.1KB read                                               │
  └─────────────────────────────────────────────────────────────────────┘
```

The entire box snaps to flat green (`\033[38;2;39;174;96m`) with the `✓` symbol.
The header changes from in-progress label ("Reading...") to completed label ("Read...").
The border, header, and result line are all green. Stays on screen.

**State 3: FAILURE (final, static)**

```
  ┌─ Search failed for 'flask async patterns' ──────────────────────────┐
  │ ✗ 2.1s | DuckDuckGo timeout                                        │
  └─────────────────────────────────────────────────────────────────────┘
```

The entire box snaps to flat red (`\033[38;2;231;76;60m`) with the `✗` symbol.
Border, tool name, and result line are all red.

### Color Palette (Flat UI)

| Role | Hex | ANSI 24-bit | Usage |
|------|-----|-------------|-------|
| In-progress A | `#95A5A6` | `\033[38;2;149;165;166m` | Gray phase of pulse |
| In-progress B | `#F39C12` | `\033[38;2;243;156;18m` | Orange phase of pulse |
| Success | `#27AE60` | `\033[38;2;39;174;96m` | Final ✓ state |
| Failure | `#E74C3C` | `\033[38;2;231;76;60m` | Final ✗ state |
| Spinner chars | — | — | `◐ ◓ ◑ ◒` (rotate at 150ms) |

### Animation Sequence

```
t=0ms     Gray   ◐ reading...
t=150ms   Gray   ◓ reading...
t=300ms   Orange ◐ reading...
t=450ms   Orange ◓ reading...
t=600ms   Gray   ◑ reading...
t=750ms   Gray   ◒ reading...
t=900ms   Orange ◐ reading...
  ... (loops until tool completes)

Tool returns → clear animation line → print final box (green ✓ or red ✗)
```

### Terminal Rendering (how the animation works)

```python
def _animate_tool_box(tool_name: str, args: dict, done_event: threading.Event):
    """Render animated in-progress box while tool executes.
    
    Runs on a daemon thread. Uses carriage return + ANSI escape
    to overwrite the result line in-place without scrolling.
    """
    GRAY = "\033[38;2;149;165;166m"
    ORANGE = "\033[38;2;243;156;18m"
    RESET = "\033[0m"
    spinners = ["◐", "◓", "◑", "◒"]
    
    action_label = _get_action_label(tool_name, args)
    
    # Print the top border (stays on screen permanently)
    header = f"  {GRAY}┌─ {action_label} {'─' * pad}┐{RESET}"
    print(header)
    
    idx = 0
    while not done_event.is_set():
        # Alternate gray/orange every 2 spinner frames (300ms)
        color = GRAY if (idx // 2) % 2 == 0 else ORANGE
        spinner = spinners[idx % 4]
        line = f"  {color}│ {spinner} reading...{' ' * pad}│{RESET}"
        sys.stdout.write(f"\r{line}")
        sys.stdout.flush()
        idx += 1
        time.sleep(0.15)


def _finalize_tool_box(success: bool, duration: float, summary: str):
    """Replace the animated line with final result and close the box."""
    GREEN = "\033[38;2;39;174;96m"
    RED = "\033[38;2;231;76;60m"
    RESET = "\033[0m"
    
    if success:
        color = GREEN
        icon = "✓"
    else:
        color = RED
        icon = "✗"
    
    # Overwrite animated line with final result
    result_line = f"  {color}│ {icon} {duration:.2f}s | {summary}{' ' * pad}│{RESET}"
    sys.stdout.write(f"\r{result_line}\n")
    
    # Print bottom border
    bottom = f"  {color}└{'─' * (box_width - 2)}┘{RESET}"
    print(bottom)
```

### Full Animated Flow (what the user sees in real-time)

```
⚙️  GPT OSS 120b — 8s

  ┌─ Reading cli_kognisant/network.py ──────────────────────────────────┐
  │ ◐ reading...                    ← (pulsing gray↔orange, spinner rotating)
```
...0.02 seconds later, the ENTIRE box redraws with completed state...
```
  ┌─ Read cli_kognisant/network.py ─────────────────────────────────────┐
  │ ✓ 0.02s | 4.1KB read                                               │
  └─────────────────────────────────────────────────────────────────────┘
  ┌─ Editing auth.py ──────────────────────────────────────────────────┐
  │ ◓ writing...                    ← (next tool starts animating)
```
...0.03 seconds later, redraws to completed...
```
  ┌─ Accepted edits to auth.py ─────────────────────────────────────────┐
  │ ✓ 0.03s | 3 edits applied                                          │
  └─────────────────────────────────────────────────────────────────────┘

⚙️  GPT OSS 120b — 4s (follow-up, +1,024 tokens from tool results)
```

### Action Label Mapping

The box header shows a human-readable action description that **changes tense on completion**.
While in progress, it's present tense ("Reading..."). On success, it changes to past/completed
tense ("Read successfully" / "Accepted edits to...").

| Tool Name | In-Progress Header | Completed Header (✓) | Failed Header (✗) |
|-----------|-------------------|---------------------|-------------------|
| `read_project_file` | `Reading auth.py` | `Read auth.py` | `Failed to read auth.py` |
| `read_global_file` | `Reading ~/.kognisant_core/tools/shell.py` | `Read ~/.kognisant_core/tools/shell.py` | `Failed to read file` |
| `read_script` | `Reading script 'data-collector'` | `Read script 'data-collector'` | `Failed to read script` |
| `edit_project_file` | `Editing auth.py` | `Accepted edits to auth.py` | `Rejected edits to auth.py` |
| `edit_global_file` | `Editing ~/.kognisant_core/tools/shell.py` | `Accepted edits to shell.py` | `Rejected edits to file` |
| `edit_script` | `Editing script 'data-collector'` | `Accepted edits to script 'data-collector'` | `Rejected edits to script` |
| `create_project_file` | `Creating tests/test_auth.py` | `Created tests/test_auth.py` | `Failed to create file` |
| `create_global_file` | `Creating global file shell.json` | `Created global file shell.json` | `Failed to create file` |
| `create_script` | `Creating script 'news-scraper'` | `Created script 'news-scraper'` | `Failed to create script` |
| `create_project_directory` | `Creating directory src/utils/` | `Created directory src/utils/` | `Failed to create directory` |
| `delete_project_path` | `Deleting old_module.py` | `Deleted old_module.py` | `Failed to delete path` |
| `delete_script` | `Deleting script 'temp-job'` | `Deleted script 'temp-job'` | `Failed to delete script` |
| `list_project_files` | `Listing project files` | `Listed project files` | `Failed to list files` |
| `list_scripts` | `Listing scripts` | `Listed scripts` | `Failed to list scripts` |
| `list_jobs` | `Listing jobs` | `Listed jobs` | `Failed to list jobs` |
| `search_web` | `Searching 'flask async patterns'` | `Found results for 'flask async patterns'` | `Search failed` |
| `browse_web_page` | `Fetching docs.python.org/...` | `Fetched docs.python.org/...` | `Failed to fetch page` |
| `open_in_native_browser` | `Opening in browser` | `Opened in browser` | `Failed to open browser` |
| `shell_execution` | `Running 'npm run lint'` | `Ran 'npm run lint'` | `Command failed` |
| `schedule_job` | `Scheduling job 'nightly-backup'` | `Scheduled job 'nightly-backup'` | `Failed to schedule job` |
| `cancel_job` | `Cancelling job 'stale-task'` | `Cancelled job 'stale-task'` | `Failed to cancel job` |
| `remove_job` | `Removing job 'old-job'` | `Removed job 'old-job'` | `Failed to remove job` |
| `job_logs` | `Fetching logs for 'my-job'` | `Fetched logs for 'my-job'` | `Failed to fetch logs` |
| `capture_active_browser_console` | `Capturing browser console` | `Captured browser console` | `Failed to capture console` |
| (any custom global tool) | `Running tool 'placeholders_io'` | `Ran tool 'placeholders_io'` | `Tool 'placeholders_io' failed` |

### Visual Example (header changes on completion)

**While executing:**
```
  ┌─ Editing auth.py ───────────────────────────────────────────────────┐
  │ ◐ writing...                                                        │
  └─────────────────────────────────────────────────────────────────────┘
```

**After success — header redraws with completed label:**
```
  ┌─ Accepted edits to auth.py ─────────────────────────────────────────┐
  │ ✓ 0.03s | 3 edits applied                                          │
  └─────────────────────────────────────────────────────────────────────┘
```

**After failure — header redraws with failed label:**
```
  ┌─ Rejected edits to auth.py ─────────────────────────────────────────┐
  │ ✗ 0.01s | old_text not found                                        │
  └─────────────────────────────────────────────────────────────────────┘
```

```python
def _get_action_label(tool_name: str, args: dict, state: str = "progress") -> str:
    """Convert raw tool name + args into a human-readable action label.
    
    Args:
        tool_name: The function name of the tool.
        args: Parsed arguments dict.
        state: "progress" (in-progress), "success" (completed), or "failure" (failed).
    """
    labels = {
        "read_project_file": {
            "progress": lambda a: f"Reading {a.get('file_path', 'file')}",
            "success": lambda a: f"Read {a.get('file_path', 'file')}",
            "failure": lambda a: f"Failed to read {a.get('file_path', 'file')}",
        },
        "edit_project_file": {
            "progress": lambda a: f"Editing {a.get('file_path', 'file')}",
            "success": lambda a: f"Accepted edits to {a.get('file_path', 'file')}",
            "failure": lambda a: f"Rejected edits to {a.get('file_path', 'file')}",
        },
        "edit_global_file": {
            "progress": lambda a: f"Editing {a.get('file_path', 'file')}",
            "success": lambda a: f"Accepted edits to {a.get('file_path', 'file')}",
            "failure": lambda a: f"Rejected edits to {a.get('file_path', 'file')}",
        },
        "edit_script": {
            "progress": lambda a: f"Editing script '{a.get('name', '?')}'",
            "success": lambda a: f"Accepted edits to script '{a.get('name', '?')}'",
            "failure": lambda a: f"Rejected edits to script '{a.get('name', '?')}'",
        },
        "create_project_file": {
            "progress": lambda a: f"Creating {a.get('file_path', 'file')}",
            "success": lambda a: f"Created {a.get('file_path', 'file')}",
            "failure": lambda a: f"Failed to create {a.get('file_path', 'file')}",
        },
        "create_global_file": {
            "progress": lambda a: f"Creating global file {a.get('file_path', '?')}",
            "success": lambda a: f"Created global file {a.get('file_path', '?')}",
            "failure": lambda a: f"Failed to create global file",
        },
        "create_script": {
            "progress": lambda a: f"Creating script '{a.get('name', '?')}'",
            "success": lambda a: f"Created script '{a.get('name', '?')}'",
            "failure": lambda a: f"Failed to create script '{a.get('name', '?')}'",
        },
        "create_project_directory": {
            "progress": lambda a: f"Creating directory {a.get('directory_path', '?')}",
            "success": lambda a: f"Created directory {a.get('directory_path', '?')}",
            "failure": lambda a: f"Failed to create directory",
        },
        "delete_project_path": {
            "progress": lambda a: f"Deleting {a.get('path', '?')}",
            "success": lambda a: f"Deleted {a.get('path', '?')}",
            "failure": lambda a: f"Failed to delete {a.get('path', '?')}",
        },
        "delete_script": {
            "progress": lambda a: f"Deleting script '{a.get('name', '?')}'",
            "success": lambda a: f"Deleted script '{a.get('name', '?')}'",
            "failure": lambda a: f"Failed to delete script '{a.get('name', '?')}'",
        },
        "read_global_file": {
            "progress": lambda a: f"Reading {a.get('file_path', 'file')}",
            "success": lambda a: f"Read {a.get('file_path', 'file')}",
            "failure": lambda a: f"Failed to read {a.get('file_path', 'file')}",
        },
        "read_script": {
            "progress": lambda a: f"Reading script '{a.get('name', '?')}'",
            "success": lambda a: f"Read script '{a.get('name', '?')}'",
            "failure": lambda a: f"Failed to read script '{a.get('name', '?')}'",
        },
        "list_project_files": {
            "progress": lambda a: "Listing project files",
            "success": lambda a: "Listed project files",
            "failure": lambda a: "Failed to list files",
        },
        "list_scripts": {
            "progress": lambda a: "Listing scripts",
            "success": lambda a: "Listed scripts",
            "failure": lambda a: "Failed to list scripts",
        },
        "list_jobs": {
            "progress": lambda a: "Listing jobs",
            "success": lambda a: "Listed jobs",
            "failure": lambda a: "Failed to list jobs",
        },
        "search_web": {
            "progress": lambda a: f"Searching '{a.get('query', '?')[:40]}'",
            "success": lambda a: f"Found results for '{a.get('query', '?')[:40]}'",
            "failure": lambda a: f"Search failed for '{a.get('query', '?')[:40]}'",
        },
        "browse_web_page": {
            "progress": lambda a: f"Fetching {a.get('url', '?')[:50]}",
            "success": lambda a: f"Fetched {a.get('url', '?')[:50]}",
            "failure": lambda a: f"Failed to fetch page",
        },
        "open_in_native_browser": {
            "progress": lambda a: "Opening in browser",
            "success": lambda a: "Opened in browser",
            "failure": lambda a: "Failed to open browser",
        },
        "shell_execution": {
            "progress": lambda a: f"Running '{a.get('command', '?')[:40]}'",
            "success": lambda a: f"Ran '{a.get('command', '?')[:40]}'",
            "failure": lambda a: f"Command failed: '{a.get('command', '?')[:40]}'",
        },
        "schedule_job": {
            "progress": lambda a: f"Scheduling job '{a.get('name', '?')}'",
            "success": lambda a: f"Scheduled job '{a.get('name', '?')}'",
            "failure": lambda a: f"Failed to schedule job '{a.get('name', '?')}'",
        },
        "cancel_job": {
            "progress": lambda a: f"Cancelling job '{a.get('name', '?')}'",
            "success": lambda a: f"Cancelled job '{a.get('name', '?')}'",
            "failure": lambda a: f"Failed to cancel job '{a.get('name', '?')}'",
        },
        "remove_job": {
            "progress": lambda a: f"Removing job '{a.get('name', '?')}'",
            "success": lambda a: f"Removed job '{a.get('name', '?')}'",
            "failure": lambda a: f"Failed to remove job '{a.get('name', '?')}'",
        },
        "job_logs": {
            "progress": lambda a: f"Fetching logs for '{a.get('name', '?')}'",
            "success": lambda a: f"Fetched logs for '{a.get('name', '?')}'",
            "failure": lambda a: f"Failed to fetch logs",
        },
        "capture_active_browser_console": {
            "progress": lambda a: "Capturing browser console",
            "success": lambda a: "Captured browser console",
            "failure": lambda a: "Failed to capture console",
        },
    }
    
    if tool_name in labels:
        return labels[tool_name][state](args)
    
    # Fallback for custom global tools
    fallback = {
        "progress": f"Running tool '{tool_name}'",
        "success": f"Ran tool '{tool_name}'",
        "failure": f"Tool '{tool_name}' failed",
    }
    return fallback[state]
```

### Error Handling During Execute

| Error | Action |
|-------|--------|
| `KognisantAPIError` (streaming fails) | Fallback to non-streaming call |
| `KeyboardInterrupt` | Stop spinner, rollback, stay in chat |
| HTTP 401 | Rollback, tell user "API key rejected" |
| HTTP 429 | Rollback, tell user "rate limited, wait" |
| HTTP 400 + "tools not supported" | Disable tools for this model, retry without tools |
| Connection error / URLError | Rollback, tell user "can't reach endpoint" |
| Timeout (no response within limit) | Record timeout, suggest model switch |
| Empty response (content is blank) | Show explicit "model returned empty" message |

### Self-Healing: Tool Support Detection

```
IF LLM returns HTTP 400 with "tools" in error message:
  1. Set model capabilities.tool_calling = False in SelfModel
  2. Remove tools from payload
  3. Retry the same request (1 retry)
  4. Print: "⚠️  [model] doesn't support tools — conversation mode"
```

---

## Phase 4 — Reflect

**Purpose:** Learn from the interaction. Update confidence, valence, reliability.
No LLM calls. Pure local computation.

### Reflect Tiers

| Tier | Frequency | What It Does | Cost |
|------|-----------|--------------|------|
| HOT | Every execution | Valence, model reliability, tool reliability | <5ms |
| WARM | Every 3rd execution | Frustration check, model deprioritization advice | <5ms |
| COLD | Every 20th execution | Health report, stale model cleanup suggestion | <10ms |

### HOT Reflect (always runs)

```python
# Valence update rules:
if success and response_time < 10:
    valence_delta = +5       # fast success
elif success and response_time < 30:
    valence_delta = +3       # moderate success
elif success and response_time >= 30:
    valence_delta = +1       # slow but worked
elif timeout:
    valence_delta = -15      # timeout penalty
elif empty_response:
    valence_delta = -10      # empty = model failure
elif cancelled_by_user:
    valence_delta = -5       # user frustration signal
elif error:
    valence_delta = -10      # generic failure

# Valence is clamped to [-100, +100]
valence = clamp(valence + valence_delta, -100, 100)
```

```python
# Model reliability Bayesian update:
# Laplace smoothing: confidence = (successes + 1) / (successes + failures + 2)
if success:
    model.successes += 1
else:
    model.failures += 1
model.reliability = (model.successes + 1) / (model.successes + model.failures + 2)
model.attempts += 1
model.avg_response_time = rolling_average(model.avg_response_time, response_time)
```

```python
# Tool reliability (for each tool used in this execution):
# Same Bayesian formula
if tool_succeeded:
    tool.successes += 1
else:
    tool.failures += 1
tool.reliability = (tool.successes + 1) / (tool.successes + tool.failures + 2)
```

### WARM Reflect (every 3rd execution)

```python
# Frustration detection:
if consecutive_failures >= 3:
    frustration = min(1.0, frustration + 0.15)
    print("  ⚠️  3 consecutive failures. Consider /model to switch.")

# Model deprioritization check:
for model in all_models:
    if model.reliability < 0.3 and model.attempts >= 5:
        print(f"  💡 {model.name} has low reliability ({model.reliability:.0%})")
        print(f"     Consider removing it or checking the API key.")
```

### COLD Reflect (every 20th execution)

```python
# Health report:
print("  📊 Session health report:")
print(f"     Total executions: {total}")
print(f"     Success rate: {successes/total:.0%}")
print(f"     Avg response time: {avg_time:.1f}s")
print(f"     Valence trend: {valence_description}")
for model in all_models:
    if model.attempts > 0:
        print(f"     {model.name}: {model.reliability:.0%} reliable ({model.attempts} calls)")
```

### Output to User

The reflect phase always prints a single summary line:
```
🔍 3.4s | valence: +27 (+5) | GPT OSS 120b ✓
```

On WARM/COLD, additional lines appear below.

---

## Phase 5 — Persist

**Purpose:** Atomically save all state changes to disk.

### What Gets Written

| File | Content | When |
|------|---------|------|
| `~/.kognisant_core/self_model.json` | Full SelfModel state | Every execution |
| `.kognisant/history/session_*.json` | Conversation messages | Every execution (via existing save_chat_session) |
| `.kognisant/context.md` | Project memory | Only on COMPLEX executions that modify project state |

### Atomic Write Protocol

Same as existing `jobs.json` pattern:
1. Write to `self_model.json.tmp`
2. `os.fsync(fd)`
3. `os.rename(tmp, self_model.json)`
4. `os.fsync(dir_fd)`

On failure: tmp is removed, previous state is preserved. No corruption possible.

---

## SelfModel Engine

### Data Structure

```json
{
  "version": 1,
  "valence": 22,
  "frustration": 0.0,
  "total_executions": 47,
  "consecutive_failures": 0,
  "last_execution_at": "2026-06-21T00:30:00Z",
  "session_start_at": "2026-06-21T00:20:00Z",
  "capability_snapshot": {
    "scripts_count": 0,
    "skills_count": 15,
    "skills_names": [
      "coding_standards", "defensive_file_write_validation",
      "document_refactoring_validation_protocol", "documentation_finalization_checklist",
      "documentation_review_closed_book_discipline", "documentation_review_scope_precision",
      "global_tool_development", "multi_phase_artifact_validation",
      "readme_drift_detection_and_sync", "resilient_swarm_phase_gates",
      "shell_execution", "simulated_learning_caps",
      "swarm_pipeline_dependency_validation", "two_phase_refactor_validation",
      "web_browser_steering"
    ],
    "custom_tools_count": 6,
    "custom_tools_names": [
      "browse_web_page", "capture_active_browser_console",
      "open_in_native_browser", "placeholders_io",
      "search_web", "shell_execution"
    ],
    "builtin_tools_count": 23,
    "active_jobs_count": 0,
    "failed_jobs_count": 0,
    "registered_projects": 3,
    "daemon_running": true
  },
  "project_state": {
    "project_root": "/Users/mosugroo/Documents/projects/py/research",
    "project_name": "research",
    "world_model_enabled": false,
    "world_model_nodes": 0,
    "active_specs": [],
    "active_goals": 0,
    "session_history_count": 12,
    "context_md_size": 2359,
    "guidelines_size": 1720
  },
  "model_reliability": {
    "nvidia/nemotron-3-ultra-550b-a55b": {
      "successes": 1,
      "failures": 4,
      "reliability": 0.29,
      "attempts": 5,
      "avg_response_time": 180.0,
      "last_success_at": "2026-06-20T22:00:00Z",
      "last_failure_at": "2026-06-21T00:25:00Z",
      "capabilities": {
        "tool_calling": true
      }
    },
    "openai/gpt-oss-120b": {
      "successes": 11,
      "failures": 1,
      "reliability": 0.86,
      "attempts": 12,
      "avg_response_time": 4.2,
      "last_success_at": "2026-06-21T00:30:00Z",
      "last_failure_at": null,
      "capabilities": {
        "tool_calling": true
      }
    }
  },
  "tool_reliability": {
    "read_project_file": {
      "successes": 23,
      "failures": 0,
      "reliability": 0.96
    },
    "search_web": {
      "successes": 5,
      "failures": 2,
      "reliability": 0.67
    },
    "shell_execution": {
      "successes": 8,
      "failures": 1,
      "reliability": 0.82
    }
  },
  "circuit_breakers": {
    "nvidia/nemotron-3-ultra-550b-a55b": {
      "state": "open",
      "failures_in_window": 5,
      "window_start": "2026-06-21T00:20:00Z",
      "open_until": "2026-06-21T00:20:30Z"
    }
  },
  "background_signals": {
    "avg_tool_reliability": 0.82,
    "failed_jobs_pressure": 0,
    "world_model_stale": false,
    "goal_acceptance_rate": 0.6
  }
}
```

### Bayesian Confidence Formula

```
reliability = (successes + 1) / (successes + failures + 2)
```

This is Laplace smoothing. With 0 data → 0.5 (unknown). Moves slowly, requires evidence.

| Successes | Failures | Reliability |
|-----------|----------|-------------|
| 0 | 0 | 0.50 (unknown) |
| 1 | 0 | 0.67 |
| 3 | 0 | 0.80 |
| 9 | 1 | 0.83 |
| 1 | 4 | 0.29 (unreliable) |
| 0 | 3 | 0.20 (very unreliable) |

### Valence Semantics

| Range | Meaning | Behaviour |
|-------|---------|-----------|
| +50 to +100 | Things are working great | Normal operation |
| 0 to +50 | Balanced | Normal operation |
| -50 to 0 | Some friction | Conservative mode (prefer reliable models) |
| -100 to -50 | Significant problems | Auto-switch to most reliable model available |

### Model Selection Algorithm

```python
def select_model(configured_model, compiled_models, self_model):
    cb = self_model.circuit_breakers.get(configured_model.name)
    
    # Circuit breaker check
    if cb and cb.state == "open" and not cb.cooldown_expired():
        return find_best_alternative(compiled_models, self_model)
    
    # Reliability check (need 3+ attempts for data)
    rel = self_model.model_reliability.get(configured_model.name)
    if rel and rel.attempts >= 3 and rel.reliability < 0.3:
        # Check if valence is negative (user already frustrated)
        if self_model.valence < 0:
            alt = find_best_alternative(compiled_models, self_model)
            if alt:
                return alt
    
    # Default: use configured model
    return configured_model


def find_best_alternative(compiled_models, self_model):
    """Pick the model with highest reliability score."""
    candidates = []
    for model in compiled_models:
        if model.name == configured_model.name:
            continue
        rel = self_model.model_reliability.get(model.name)
        if rel:
            candidates.append((model, rel.reliability))
        else:
            candidates.append((model, 0.5))  # unknown = neutral
    
    if not candidates:
        return None
    
    # Sort by reliability descending
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]
```

---

## FastPath Classifier

### Decision Rules (evaluated in order)

```python
def classify(message: str) -> str:
    words = message.lower().split()
    word_count = len(words)
    
    # ─── SIMPLE gate (must pass ALL conditions) ───
    if word_count <= 6:
        has_action_verb = any(w in ACTION_VERBS for w in words)
        has_file_ref = any(FILE_PATTERN.search(w) for w in words)
        has_code_token = any(CODE_PATTERN.search(w) for w in words)
        has_tech_question = _is_technical_question(words)
        
        if not has_action_verb and not has_file_ref and not has_code_token and not has_tech_question:
            return "SIMPLE"
    
    # ─── COMPLEX gate (any ONE condition triggers) ───
    if any(w in ACTION_VERBS for w in words):
        return "COMPLEX"
    if any(FILE_PATTERN.search(w) for w in words):
        return "COMPLEX"
    if word_count > 30:
        return "COMPLEX"
    if _is_multi_sentence(message):
        return "COMPLEX"
    if any(CODE_PATTERN.search(w) for w in words):
        return "COMPLEX"
    
    # ─── Default: CONTEXT ───
    # Not simple enough for SIMPLE, not actionable enough for COMPLEX.
    # Includes: explanations, recaps, questions about concepts, status queries.
    return "CONTEXT"
```

### Pattern Constants

```python
ACTION_VERBS = {
    "fix", "create", "read", "edit", "write", "modify", "delete",
    "remove", "refactor", "implement", "build", "run", "execute",
    "test", "deploy", "add", "update", "install", "search", "browse",
    "download", "schedule", "script", "migrate", "optimize", "debug",
    "change", "move", "rename", "copy", "generate", "make",
}

# Matches file-like patterns: foo.py, src/bar.js, ../config.yaml
FILE_PATTERN = re.compile(r'[\w\-]+\.\w{1,5}|[\w\-]+/[\w\-]+')

# Matches code-like tokens: my_function, ClassName, __init__
CODE_PATTERN = re.compile(r'[a-z]+_[a-z]+|[A-Z][a-z]+[A-Z]|__\w+__')
```

### Classification Examples

| Input | Words | Triggers | Result |
|-------|-------|----------|--------|
| "hello" | 1 | none | SIMPLE |
| "hi there" | 2 | none | SIMPLE |
| "thanks" | 1 | none | SIMPLE |
| "good morning" | 2 | none | SIMPLE |
| "ok got it" | 3 | none | SIMPLE |
| "what are we working on?" | 6 | none (no action verb) | SIMPLE... wait — |

**Correction:** "what are we working on?" is 6 words and has no action verbs, but it needs project context. The SIMPLE gate needs one more check:

```python
# Additional SIMPLE exclusion: project-reference patterns
PROJECT_REFS = {"we", "our", "project", "working", "progress", "recap", "status"}

if word_count <= 6:
    has_project_ref = len(set(words) & PROJECT_REFS) >= 1
    ...
    if not has_action_verb and not has_file_ref and not has_code_token 
       and not has_tech_question and not has_project_ref:
        return "SIMPLE"
```

**Corrected examples:**

| Input | Words | Triggers | Result |
|-------|-------|----------|--------|
| "hello" | 1 | none | SIMPLE |
| "thanks" | 1 | none | SIMPLE |
| "what are we working on?" | 6 | "we", "working" → project ref | CONTEXT |
| "explain decorators" | 2 | none, but >educational → fails SIMPLE check? | — |

**Issue:** "explain decorators" is 2 words, no action verbs, no file refs. It would be SIMPLE. But it's a knowledge question that benefits from context.

**Resolution:** This is actually fine. "explain decorators" can be answered with a minimal system prompt. The model knows what decorators are without project context. SIMPLE is correct here — and will get a fast response.

If the user meant "explain how we use decorators in THIS project" — they'd say more words, which would push it past SIMPLE.

---

## Circuit Breaker

### State Machine

```
     ┌─────────┐        5 failures         ┌────────┐
     │ CLOSED  │─────── in 30s window ─────→│  OPEN  │
     │(normal) │                            │(blocked)│
     └────┬────┘                            └────┬────┘
          │                                      │
          │   ┌──────────┐  success              │ 30s cooldown
          │   │HALF_OPEN │←─────────────────────-┘  expires
          │   │(1 test)  │
          │   └─────┬────┘
          │         │ failure
          │         └──────→ OPEN (restart cooldown)
          │
          └──── success ──── stays CLOSED
```

### Parameters

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Failure threshold | 5 | Enough evidence of a real problem |
| Window duration | 30 seconds | Groups rapid consecutive failures |
| Cooldown (open → half_open) | 30 seconds | Give endpoint time to recover |
| Half-open test | 1 attempt | Minimal probe |

### Implementation

```python
class CircuitBreaker:
    def __init__(self, threshold=5, window_seconds=30, cooldown_seconds=30):
        self.threshold = threshold
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        self.state = "closed"         # "closed" | "open" | "half_open"
        self.failures_in_window = 0
        self.window_start = None
        self.open_until = None
    
    def record_failure(self, now):
        if self.window_start is None or (now - self.window_start) > self.window_seconds:
            # Start new window
            self.window_start = now
            self.failures_in_window = 1
        else:
            self.failures_in_window += 1
        
        if self.failures_in_window >= self.threshold:
            self.state = "open"
            self.open_until = now + self.cooldown_seconds
    
    def record_success(self):
        self.state = "closed"
        self.failures_in_window = 0
        self.window_start = None
    
    def can_attempt(self, now):
        if self.state == "closed":
            return True
        if self.state == "open":
            if now >= self.open_until:
                self.state = "half_open"
                return True  # Allow one test
            return False
        if self.state == "half_open":
            return True  # Currently testing
        return False
```

---

## User Experience — What the User Sees

### Minimal Output (SIMPLE success)

```
You > hello
⚡ GPT OSS 120b | valence: +22 | 15 skills, 6 tools, 0 jobs active
📋 SIMPLE → ~210 tokens input
⚙️  GPT OSS 120b — 2s

Kognisant >
Hello! What can I help you with?

🔍 2.1s | 210 in → 12 out | valence: +27 (+5)
```

### Standard Output (CONTEXT success)

```
You > what are we working on?
⚡ GPT OSS 120b | valence: +27 | project: research (context.md loaded)
📋 CONTEXT → ~1,840 tokens input
⚙️  GPT OSS 120b — 5s

Kognisant >
Based on the project memory, we're currently working on...
[full response]

🔍 5.3s | 1,840 in → 187 out | valence: +30 (+3)
```

### Full Output (COMPLEX with tools)

```
You > fix the import error in network.py
⚡ GPT OSS 120b | valence: +30 | project: research | WM: 47 nodes
📋 COMPLEX → ~8,420 tokens input (sys: 2,100 + tools: 4,200 + hist: 1,800 + msg: 320)
⚙️  GPT OSS 120b — 8s

  ┌─ Read cli_kognisant/network.py ───────────────────────────────────────┐
  │ ✓ 0.02s | 4.1KB read                                               │
  └─────────────────────────────────────────────────────────────────────┘

⚙️  GPT OSS 120b — 4s (follow-up, +1,024 tokens from tool result)

Kognisant >
I found the issue. The import on line 3...
[full response]

🔍 12.4s | 9,444 in → 342 out | 1 tool | valence: +33 (+3) | model: 0.86 reliability
```

**Multi-tool example:**

```
You > refactor auth.py and update the tests
⚡ GPT OSS 120b | valence: +33 | project: research
📋 COMPLEX → ~9,100 tokens input (sys: 2,100 + tools: 4,200 + hist: 2,200 + msg: 600)
⚙️  GPT OSS 120b — 12s

  ┌─ Read auth.py ────────────────────────────────────────────────────────┐
  │ ✓ 0.01s | 2.8KB read                                               │
  └─────────────────────────────────────────────────────────────────────┘
  ┌─ Read tests/test_auth.py ───────────────────────────────────────────┐
  │ ✓ 0.01s | 1.2KB read                                               │
  └─────────────────────────────────────────────────────────────────────┘
  ┌─ Accepted edits to auth.py ─────────────────────────────────────────┐
  │ ✓ 0.03s | 3 edits applied                                          │
  └─────────────────────────────────────────────────────────────────────┘
  ┌─ Accepted edits to tests/test_auth.py ──────────────────────────────┐
  │ ✓ 0.02s | 2 edits applied                                          │
  └─────────────────────────────────────────────────────────────────────┘

⚙️  GPT OSS 120b — 6s (follow-up, +3,840 tokens from tool results)

Kognisant >
Done. I refactored the auth module to use...
[full response]

🔍 18.2s | 12,940 in → 512 out | 4 tools | valence: +36 (+3) | model: 0.87 reliability
```

**Tool failure example:**

```
  ┌─ Search failed for 'flask async patterns' ──────────────────────────┐
  │ ✗ 2.1s | DuckDuckGo timeout                                        │
  └─────────────────────────────────────────────────────────────────────┘
  ┌─ Read app.py ───────────────────────────────────────────────────────┐
  │ ✓ 0.01s | 5.2KB read                                               │
  └─────────────────────────────────────────────────────────────────────┘
```

### Auto-Switch Output

```
You > hello
⚡ Switching → GPT OSS 120b (GROQ)
  ⚠️  nvidia/nemotron-3-ultra-550b-a55b: 1/4 success, avg 180s, circuit: OPEN
📋 SIMPLE → ~210 tokens input
⚙️  GPT OSS 120b — 1s

Kognisant >
Hey! What can I do for you?

🔍 1.8s | 210 in → 9 out | valence: -3 (+5) | switched model
```

### Failure Output

```
You > hello
⚡ Nemotron-550B | valence: +12 | circuit: half_open (testing)
📋 SIMPLE → ~210 tokens input
⚙️  Nemotron-550B — 30s — TIMEOUT

⚠️  No response in 30s (SIMPLE timeout).
   Tip: /model to switch to a faster model.

🔍 30.0s | 210 in → 0 out | TIMEOUT | valence: -3 (-15)
   nvidia/nemotron-3-ultra-550b-a55b: 0.40 reliability (2/5) | circuit → OPEN
```

### Verbosity Control

The status lines (⚡📋⚙️🔍) are always shown. They are ONE line each, never multi-line.
The response from the model is displayed fully with markdown rendering as before.
Reflect output is a single compact line unless WARM/COLD has observations to share.

---

## Telemetry & Transparency

### Principle

**Every piece of useful data the system knows is surfaced to the user in real-time
AND persisted to disk for backend analysis.** No black boxes. No hidden state.

The user should be able to answer at any point:
- What model am I using and why?
- How many tokens did that cost me?
- How long did each phase take?
- Is this model reliable? Should I switch?
- What tools were used and did they work?
- Is my system healthy overall?

### Real-Time Display (what the user sees per execution)

| Phase | Data Shown |
|-------|-----------|
| ⚡ Bootstrap | Model name, provider, valence, capability counts, auto-switch reason (if any), circuit breaker state |
| 📋 Plan | Classification (SIMPLE/CONTEXT/COMPLEX), estimated input token count with breakdown |
| ⚙️ Execute | Model name, elapsed seconds (live updating), follow-up round token additions |
| 🔍 Reflect | Total wall time, tokens in → tokens out, tool call count, valence change, model reliability update |

### Token Counting

Tokens are estimated locally (no API call needed):
```python
def estimate_tokens(text: str) -> int:
    """Estimate token count using the ~4 chars per token heuristic.
    
    This is accurate within ±10% for English text and code.
    Exact counts would require a tokenizer (tiktoken etc.) which
    would add a dependency — we stay stdlib-only.
    """
    return len(text) // 4
```

Token breakdown displayed in Plan phase:
```
📋 COMPLEX → ~8,420 tokens input (sys: 2,100 + tools: 4,200 + hist: 1,800 + msg: 320)
```

Token summary in Reflect:
```
🔍 12.4s | 9,444 in → 342 out | ...
```

For COMPLEX with tool loops, token counts accumulate across rounds:
```
⚙️  GPT OSS 120b — 4s (follow-up, +1,024 tokens from tool result)
```

### Persisted Telemetry (stored per execution)

Every execution appends a record to `~/.kognisant_core/telemetry.jsonl` (JSON Lines):

```json
{
  "timestamp": "2026-06-21T00:30:15Z",
  "execution_id": "a7f3b...",
  "project": "research",
  "classification": "COMPLEX",
  "model": "openai/gpt-oss-120b",
  "provider": "GROQ",
  "auto_switched": false,
  "tokens_in": 9444,
  "tokens_out": 342,
  "token_breakdown": {
    "system_prompt": 2100,
    "tools_schema": 4200,
    "history": 1800,
    "user_message": 320,
    "tool_results": 1024
  },
  "response_time_ms": 12400,
  "phase_times_ms": {
    "bootstrap": 18,
    "plan": 2,
    "execute": 12350,
    "reflect": 5,
    "persist": 12
  },
  "tool_calls": [
    {
      "name": "read_project_file",
      "args_summary": "cli_kognisant/network.py",
      "success": true,
      "duration_ms": 20,
      "result_size_bytes": 4100
    }
  ],
  "success": true,
  "error": null,
  "timed_out": false,
  "cancelled": false,
  "valence_before": 30,
  "valence_after": 33,
  "valence_delta": 3,
  "model_reliability_after": 0.86,
  "circuit_breaker_state": "closed"
}
```

### Telemetry File Management

| Aspect | Detail |
|--------|--------|
| Format | JSON Lines (.jsonl) — one JSON object per line, append-only |
| Location | `~/.kognisant_core/telemetry.jsonl` |
| Rotation | When file exceeds 5MB, rename to `telemetry.1.jsonl` (keep 1 backup) |
| Retention | Last 1000 executions minimum |
| Write mode | Append-only (`open("a")`) — no locking needed, no corruption risk |
| Failure | If write fails, log warning, never interrupt execution |

### `/telemetry` Slash Command

A new slash command surfaces aggregated telemetry in chat:

```
You > /telemetry

📊 Telemetry Summary (last 50 executions)
──────────────────────────────────────────────────────────
  Total executions:     50
  Success rate:         88% (44/50)
  Avg response time:    6.2s
  Total tokens in:      142,300
  Total tokens out:     12,450
  
  Model breakdown:
    GPT OSS 120b (GROQ):    42 calls | 92% success | avg 4.1s | 98,200 tokens in
    Nemotron-550B (NVidia):   8 calls | 50% success | avg 34s  | 44,100 tokens in
  
  Classification breakdown:
    SIMPLE:   18 (avg 210 tokens, avg 2.1s)
    CONTEXT:  22 (avg 1,840 tokens, avg 5.4s)
    COMPLEX:  10 (avg 8,400 tokens, avg 14.2s)
  
  Tool usage:
    read_project_file:    12 calls | 100% success
    edit_project_file:     4 calls | 100% success
    search_web:            2 calls | 50% success
    shell_execution:       3 calls | 100% success
  
  Valence trend: +12 → +33 (↑ improving)
  Circuit breakers: all closed
```

### `/telemetry <model>` — Per-Model Deep Dive

```
You > /telemetry GPT OSS 120b

📊 GPT OSS 120b (GROQ) — Telemetry
──────────────────────────────────────────────────────────
  Total calls:          42
  Success rate:         92% (39/42)
  Reliability score:    0.86 (Bayesian)
  Avg response time:    4.1s
  Avg tokens in:        2,338
  Avg tokens out:       156
  Fastest response:     0.8s (SIMPLE)
  Slowest response:     28s (COMPLEX)
  Timeouts:             1
  Empty responses:      2
  Circuit breaker:      CLOSED
  Last used:            2 minutes ago
  Capabilities:         tool_calling ✓, reasoning ✓
```

### Telemetry Data in Reflect Phase (Backend)

The Reflect phase computes and persists:

```python
# Per-execution telemetry record
telemetry_record = {
    "timestamp": utc_now_iso(),
    "execution_id": generate_uuid(),
    "project": ctx.project_info["name"] if ctx.project_info else None,
    "classification": ctx.classification,
    "model": ctx.active_model["name"],
    "provider": ctx.active_model["provider"],
    "auto_switched": ctx.auto_switched,
    "tokens_in": ctx.total_tokens_in,
    "tokens_out": ctx.total_tokens_out,
    "token_breakdown": ctx.token_breakdown,
    "response_time_ms": int(ctx.response_time * 1000),
    "phase_times_ms": ctx.phase_times,
    "tool_calls": ctx.tool_telemetry,
    "success": ctx.success,
    "error": ctx.error_type,          # "timeout" | "empty" | "api_error" | None
    "timed_out": ctx.timed_out,
    "cancelled": ctx.cancelled,
    "valence_before": valence_before,
    "valence_after": self_model.valence,
    "valence_delta": valence_delta,
    "model_reliability_after": model_rel.reliability,
    "circuit_breaker_state": cb.state,
}
append_telemetry(telemetry_record)
```

### How Telemetry Informs Decisions

The telemetry data isn't just for display — it drives decisions:

| Data Point | Decision It Informs |
|-----------|-------------------|
| `model_reliability` (Bayesian) | Auto-switch in Bootstrap when < 0.3 |
| `avg_response_time` per model | Timeout selection: use model's P95 as upper bound |
| `tokens_in` per classification | Validate token budget estimates are accurate |
| `tool_calls[].success` | Tool reliability → deprioritize unreliable tools |
| `circuit_breaker_state` | Skip dead models entirely |
| `valence_trend` | Conservative vs exploratory behaviour |
| `classification` distribution | Validate classifier isn't misrouting |
| `empty responses` count | Detect model-specific issues before user notices |

---

## File Structure

### New Files

```
cli_kognisant/
  runtime.py                  ← 5-phase lifecycle orchestrator
  fast_path_classifier.py     ← SIMPLE / CONTEXT / COMPLEX classification
  self_model_engine.py        ← SelfModel load/save, Bayesian updates, 
                                 valence, circuit breakers, model selection
  reflect_engine.py           ← HOT / WARM / COLD reflection logic
  telemetry.py                ← Telemetry recording, rotation, aggregation, /telemetry command
```

### Modified Files

```
cli_kognisant/
  chat.py                     ← Replace inner while-loop with runtime.execute() call
  colors.py                   ← Already modified: Spinner with show_elapsed
  network.py                  ← Already modified: 300s timeout
```

### New Data File

```
~/.kognisant_core/
  self_model.json             ← Persisted SelfModel state (created on first run)
  telemetry.jsonl             ← Append-only execution telemetry (JSON Lines)
```

---

## Data Schemas

### ExecutionResult (returned by runtime.execute)

```python
@dataclass
class ExecutionResult:
    success: bool               # True if model returned non-empty content
    response: str               # Text response (for non-streamed display)
    streamed: bool              # True if already printed to stdout
    error: str | None           # Formatted error message (for display)
    classification: str         # "SIMPLE" | "CONTEXT" | "COMPLEX"
    model_used: str             # Display name of model that handled this
    response_time: float        # Wall-clock seconds from Execute start to end
    tool_calls_made: int        # Number of tool calls executed (0 for SIMPLE/CONTEXT)
    valence_delta: int          # Valence change applied in Reflect
    timed_out: bool             # True if execution hit timeout
    cancelled: bool             # True if user pressed Ctrl+C
```

### ExecutionContext (internal state passed between phases)

```python
@dataclass
class ExecutionContext:
    user_message: str
    messages: list[dict]        # Mutable reference to conversation history
    model_config: dict          # User's configured default model
    project_info: dict | None   # Project root, file listing
    session_file: str | None    # For save_chat_session
    checkpoint_idx: int         # Messages length before this turn
    
    # Set by Bootstrap:
    active_model: dict          # Model actually being used (may differ from configured)
    self_model: dict            # Loaded SelfModel state
    auto_switched: bool         # True if model was auto-selected
    switch_reason: str          # Why the switch happened
    
    # Set by Plan:
    classification: str         # "SIMPLE" | "CONTEXT" | "COMPLEX"
    system_prompt: str          # Constructed system prompt
    tools: list[dict] | None   # Tool schemas (None for SIMPLE/CONTEXT)
    api_messages: list[dict]    # Pruned message history for API
    timeout: int                # Seconds before giving up
    
    # Set by Execute:
    success: bool
    response: str
    streamed: bool
    response_time: float
    tool_calls_made: int
    error: str | None
    timed_out: bool
    cancelled: bool
    tools_used: list[dict]     # [{name, success, time}] for reflect
```

---

## Integration Points

### How runtime.py hooks into chat.py

**Before (current):**
```python
# In run_api_chat(), inside the main while True loop:
messages.append({"role": "user", "content": cleaned_input})
spinner = Spinner()
spinner.start()
try:
    tool_calls = None
    while True:
        # ... 200 lines of streaming, tool loops, error handling ...
except KeyboardInterrupt:
    ...
except Exception as e:
    ...
```

**After (new):**
```python
# In run_api_chat(), inside the main while True loop:
from .runtime import execute_message

result = execute_message(
    user_message=cleaned_input,
    messages=messages,
    model_config=model_config,
    project_info=project_info,
    session_file=session_file,
)

if result.success:
    if not result.streamed:
        print(f"{Colors.CYAN}Kognisant >{Colors.RESET}\n{render_markdown(result.response)}\n")
elif result.cancelled:
    pass  # Already handled inside runtime
elif result.error:
    print(f"\n{result.error}\n")
```

The runtime handles ALL of:
- Message classification
- System prompt construction
- Model selection
- Streaming output
- Tool execution loops
- Error handling
- Ctrl+C handling
- Conversation rollback
- Session saving
- SelfModel updates

The chat loop becomes a thin wrapper: read input → check slash commands → call runtime → display if needed.

### How runtime.py calls existing modules

```
runtime.py
  │
  ├── self_model_engine.py
  │     └── load() / save() / update_valence() / select_model() / record_result()
  │
  ├── fast_path_classifier.py
  │     └── classify(message) → "SIMPLE" | "CONTEXT" | "COMPLEX"
  │
  ├── config.py
  │     ├── get_compiled_models()
  │     ├── load_project_context()
  │     ├── load_project_memory_guidelines()
  │     └── get_project_info()
  │
  ├── network.py
  │     ├── query_model_api_stream()
  │     └── query_model_api_raw()
  │
  ├── tools.py
  │     ├── get_active_tools()
  │     └── execute_tool()
  │
  ├── reflect_engine.py
  │     └── reflect_hot() / reflect_warm() / reflect_cold()
  │
  └── colors.py
        ├── Spinner(show_elapsed=True)
        └── render_markdown()
```

### How existing /agent command is NOT affected

The `/agent` command is handled by `process_slash_commands()` BEFORE the runtime is called.
The PERP orchestration in `agents.py` is a separate system for multi-file autonomous tasks.
It is not modified by this refactor.

### How existing /spec command is NOT affected

Same — handled by slash commands, separate pipeline.

### How the daemon/jobs system is NOT affected

The daemon runs in a separate process. The runtime only governs interactive chat messages.
Background jobs use `ProcessManager` and the job queue — completely orthogonal.

---

## Implementation Order

Sequential. Each step produces a testable, independently-verifiable module.

### Step 1: `self_model_engine.py`

**Implements:** SelfModel data structure, load/save, Bayesian updates, valence management,
circuit breaker logic, model selection algorithm.

**Dependencies:** None (only uses stdlib + models in same file).

**Testable via:** Unit tests with mock data. No network. No LLM.

**Delivers:**
- `SelfModel` dataclass
- `SelfModelEngine.load(path) → SelfModel`
- `SelfModelEngine.save(model, path)`
- `SelfModelEngine.record_success(model_name, response_time)`
- `SelfModelEngine.record_failure(model_name, failure_type)`
- `SelfModelEngine.record_tool_result(tool_name, success)`
- `SelfModelEngine.update_valence(delta)`
- `SelfModelEngine.apply_decay()`
- `SelfModelEngine.select_model(configured, compiled_models) → model_config`
- `CircuitBreaker.can_attempt(now) → bool`
- `CircuitBreaker.record_failure(now)`
- `CircuitBreaker.record_success()`

---

### Step 2: `fast_path_classifier.py`

**Implements:** Rule-based message classification.

**Dependencies:** None (only uses stdlib `re`).

**Testable via:** Pure function tests. Input string → output classification.

**Delivers:**
- `classify(message: str) → "SIMPLE" | "CONTEXT" | "COMPLEX"`
- `ACTION_VERBS: set[str]`
- `FILE_PATTERN: re.Pattern`
- `CODE_PATTERN: re.Pattern`
- `PROJECT_REFS: set[str]`

---

### Step 3: `reflect_engine.py`

**Implements:** HOT/WARM/COLD reflection logic.

**Dependencies:** `self_model_engine.py`

**Testable via:** Unit tests with mock SelfModel state.

**Delivers:**
- `reflect_hot(self_model, result_data) → valence_delta`
- `reflect_warm(self_model) → list[str]` (observations)
- `reflect_cold(self_model) → list[str]` (health report lines)
- `should_run_warm(total_executions) → bool`
- `should_run_cold(total_executions) → bool`

---

### Step 3.5: `telemetry.py`

**Implements:** Telemetry recording, file management, aggregation queries, `/telemetry` command.

**Dependencies:** None (stdlib only: json, os, time).

**Testable via:** Unit tests — append record, read back, aggregation functions.

**Delivers:**
- `append_telemetry(record: dict)` → appends JSON line to telemetry.jsonl
- `rotate_if_needed()` → renames to .1.jsonl if >5MB
- `estimate_tokens(text: str) → int` → char_count // 4 heuristic
- `compute_token_breakdown(system_prompt, tools, history, user_msg) → dict`
- `load_recent_telemetry(count=50) → list[dict]` → reads last N records
- `aggregate_telemetry(records) → dict` → computes summary stats
- `format_telemetry_summary(records) → str` → formatted for `/telemetry` display
- `format_model_telemetry(records, model_name) → str` → per-model deep dive

---

### Step 4: `runtime.py`

**Implements:** The 5-phase orchestrator. This is the core integration module.

**Dependencies:** All of the above + `network.py`, `tools.py`, `config.py`, `colors.py`

**Testable via:** Integration tests with mocked network layer.

**Delivers:**
- `execute_message(user_message, messages, model_config, project_info, session_file) → ExecutionResult`

**Internal structure:**
```python
def execute_message(...) -> ExecutionResult:
    ctx = ExecutionContext(...)
    
    # Phase 1
    _bootstrap(ctx)
    
    # Phase 2
    _plan(ctx)
    
    # Phase 3
    try:
        _execute(ctx)
    except KeyboardInterrupt:
        _handle_cancellation(ctx)
        return ExecutionResult(cancelled=True, ...)
    
    # Phase 4
    _reflect(ctx)
    
    # Phase 5
    _persist(ctx)
    
    return ExecutionResult(
        success=ctx.success,
        response=ctx.response,
        streamed=ctx.streamed,
        ...
    )
```

---

### Step 5: Wire into `chat.py`

**Implements:** Replace the ~200-line inner while loop with a single `execute_message()` call.
Add `/telemetry` slash command.

**Dependencies:** `runtime.py` fully working + `telemetry.py`.

**Testable via:** Manual integration testing (start chat, try various messages).

**What changes in chat.py:**
1. Remove the inner `while True` tool loop
2. Remove the `try/except Exception` error handling block
3. Remove the `try/except KeyboardInterrupt` block
4. Remove `_prepare_api_messages()` (moved into runtime)
5. Remove `_streamed_response` flag management
6. Add: `from .runtime import execute_message`
7. Add: single call + result handling (5 lines)
8. Add: `/telemetry [model]` slash command handler

**What stays the same in chat.py:**
- Outer `while True` input loop
- Slash command handling (extended with `/telemetry`)
- `prompt_boxed_input()`
- Session start (system prompt, goals display)
- `select_model()` function
- `build_system_prompt()` (called by runtime, not by chat.py directly)

---

## What We Skip (and Why)

### Existing System Inventory (what we've already built)

Before listing what we skip, here's what already exists and how it maps into the runtime:

| Subsystem | Status | Location | Bootstrap Loads | Runtime Uses |
|-----------|--------|----------|-----------------|--------------|
| **Scripts** | ✅ Built | `~/.kognisant_core/scripts/` | Count available scripts | COMPLEX: tool `create_script`, `schedule_job` |
| **Skills** (15) | ✅ Built | `~/.kognisant_core/skills/*.md` | Names list | CONTEXT/COMPLEX: injected into system prompt |
| **Custom Tools** (6) | ✅ Built | `~/.kognisant_core/tools/*.json+.py` | Count + names | COMPLEX: merged into tool set via `get_active_tools()` |
| **Built-in Tools** (23) | ✅ Built | `tools.py` TOOLS_SPEC | Always available | COMPLEX: full set sent to model |
| **Job Queue** | ✅ Built | `~/.kognisant_core/jobs.json` | Active job count | COMPLEX: `schedule_job`, `list_jobs` tools |
| **Daemon** | ✅ Built | `daemon.py` | Running state check | Background (not part of chat runtime) |
| **World Model** | ✅ Built | `<project>/.kognisant/world_model/` | Node/edge count, staleness | Background (daemon maintains it) |
| **Goal Engine** | ✅ Built | `goal_engine.py` | Active goals count | Session-start display, `/goals` command |
| **Observer** | ✅ Built | `observer.py` | — | PERP traces, static analysis (daemon) |
| **SDD/Specs** | ✅ Built | `<project>/.kognisant/specs/` | Active spec list | `/spec` command, PERP integration |
| **Context.md** | ✅ Built | `<project>/.kognisant/context.md` | Full content | CONTEXT/COMPLEX: in system prompt |
| **Memory Guidelines** | ✅ Built | `<project>/.kognisant/memory-guidlines.md` | Full content | CONTEXT/COMPLEX: in system prompt |
| **Chat History** | ✅ Built | `<project>/.kognisant/history/` | Session count | Experience level signal |
| **Model Pool** | ✅ Built | `~/.kognisant_core/models_pool.json` | Compiled models | Model selection in Bootstrap |
| **PERP Agent** | ✅ Built | `agents.py` | — | `/agent` command (separate from runtime) |
| **Network Layer** | ✅ Built | `network.py` | — | Execute phase: streaming + fallback |
| **Graduated Autonomy** | ✅ Built | `goal_engine.py` | Autonomy config | Goal execution decisions |
| **Learning Loop** | ✅ Built | `goal_engine.py` | Acceptance rates | Valence background signal |

### What the Runtime Brings (NEW — not yet built)

| Component | Purpose | Replaces |
|-----------|---------|----------|
| **`runtime.py`** | 5-phase lifecycle orchestrator | The 200-line while loop in chat.py |
| **`fast_path_classifier.py`** | SIMPLE/CONTEXT/COMPLEX routing | Nothing (all messages were treated identically) |
| **`self_model_engine.py`** | Per-model Bayesian reliability, valence, circuit breakers | Nothing (no learning existed) |
| **`reflect_engine.py`** | HOT/WARM/COLD post-execution learning | Nothing (no reflection existed) |
| **`self_model.json`** | Persistent cognitive state | Nothing (every session started blind) |

### How Existing Subsystems Feed Into the Runtime

```
┌─────────────────────────────────────────────────────────────────────┐
│                      BOOTSTRAP reads:                                │
│                                                                     │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐     │
│  │ self_model  │  │ models_pool  │  │ projects.json          │     │
│  │   .json     │  │   .json      │  │ (registered projects)  │     │
│  └─────────────┘  └──────────────┘  └────────────────────────┘     │
│                                                                     │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐     │
│  │ skills/     │  │ tools/       │  │ scripts/               │     │
│  │ (15 .md)    │  │ (6 custom)   │  │ (0 scripts currently)  │     │
│  └─────────────┘  └──────────────┘  └────────────────────────┘     │
│                                                                     │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐     │
│  │ jobs.json   │  │ daemon.pid   │  │ autonomy_config.json   │     │
│  │ (4 jobs)    │  │ (running)    │  │ (graduated autonomy)   │     │
│  └─────────────┘  └──────────────┘  └────────────────────────┘     │
│                                                                     │
│  PROJECT-LEVEL:                                                     │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐     │
│  │ context.md  │  │ memory-      │  │ specs/ (active specs)  │     │
│  │ (Membrain)  │  │ guidlines.md │  │                        │     │
│  └─────────────┘  └──────────────┘  └────────────────────────┘     │
│                                                                     │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐     │
│  │ world_model/│  │ goals/       │  │ history/ (sessions)    │     │
│  │ (if enabled)│  │ learning.json│  │                        │     │
│  └─────────────┘  └──────────────┘  └────────────────────────┘     │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         PLAN uses:                                   │
│                                                                     │
│  Classification result determines which of the above gets           │
│  injected into the system prompt:                                   │
│                                                                     │
│  SIMPLE:  Nothing from above (just agent identity)                  │
│  CONTEXT: context.md + guidelines + project name + file listing     │
│  COMPLEX: All of the above + tool schemas + skill names             │
│                                                                     │
│  The model KNOWS what capabilities are available because            │
│  the system prompt tells it:                                        │
│    "You have 15 skills, 6 custom tools, 23 built-in tools,         │
│     a world model with 47 nodes, 2 active specs, and               │
│     4 registered jobs."                                             │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       EXECUTE uses:                                  │
│                                                                     │
│  COMPLEX path calls execute_tool() which dispatches to:             │
│    - Built-in handlers (file CRUD, web browsing, etc.)              │
│    - Script tools (create_script, schedule_job, etc.)               │
│    - Custom global tools (subprocess with JSON arg)                 │
│                                                                     │
│  Tool results feed back into the conversation for follow-up.        │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       REFLECT updates:                               │
│                                                                     │
│  self_model.json:                                                   │
│    - model_reliability (Bayesian update)                            │
│    - tool_reliability (per tool used in this execution)             │
│    - valence (based on success + background signals)                │
│    - circuit_breakers (failure window tracking)                     │
│                                                                     │
│  Background valence signals from:                                   │
│    - tool_reliability average (system health)                       │
│    - jobs in failed state (daemon health)                           │
│    - world model staleness (maintenance health)                     │
│    - learning loop acceptance rate (suggestion quality)             │
└─────────────────────────────────────────────────────────────────────┘
```


| Cloud Feature | Why We Skip It |
|---------------|----------------|
| Personas (multi-user identity) | Single-user CLI. No tenant isolation needed. |
| CRDT merge | Single process. No concurrent writes to same state. |
| Billing / credit checks | Free tool. No monetization. |
| SILENCE protocol | No external communications to lock down. |
| FORTRESS protocol | No sandbox escape risk in CLI. |
| OMEGA (fork-and-compare) | Overkill for CLI. Auto-switch handles recovery. |
| AffectEngine (6 dimensions) | Start with valence + frustration only. Add later if needed. |
| Prompt evolution / mutation | Not needed for responsiveness. Future enhancement. |
| ConceptEngine | Not related to response speed. Already have WorldModel. |
| MCP tools | Separate concern. Not blocking responsiveness. |
| A2A messaging (MERCURY) | Single agent. No inter-agent communication. |
| Feature flags | Everything is always on. Single binary. |
| Distributed locks | Single process. No contention possible. |
| Redis/Firebase/Firestore | File-based storage. JSON + atomic writes. |
| Audit logging | Not needed for personal CLI tool. |
| Prompt A/B testing | Future enhancement. Not blocking core issue. |

### What We Keep (adapted for CLI)

| Cloud Feature | CLI Adaptation |
|---------------|----------------|
| 5-phase lifecycle | Identical structure: Bootstrap → Plan → Execute → Reflect → Persist |
| FastPathClassifier | Rule-based (no LLM needed). SIMPLE / CONTEXT / COMPLEX. |
| SelfModel (metacognition) | Per-model Bayesian reliability. Valence. Tool reliability. |
| Circuit breakers | Per-model. 5 failures in 30s → open for 30s. |
| Valence-driven behaviour | Negative valence → prefer reliable models. |
| Model auto-fallback | Switch to best alternative when configured model is unreliable. |
| Reflect phases (HOT/WARM/COLD) | Every / every 3rd / every 20th execution. |
| Transparent status at every phase | User always sees what's happening and why. |
| Graceful Ctrl+C | Clean rollback, stay in chat. |
| Self-healing tool detection | Disable tools if model doesn't support them, retry. |
| Timeout per complexity class | SIMPLE=30s, CONTEXT=60s, COMPLEX=120s. |
| Empty response detection | Explicit message instead of blank screen. |
| Atomic persistence | tmp + fsync + rename. No corruption on crash. |

---

## Design Principles

1. **Never show a black screen.**
   Every phase prints exactly one status line. The user always knows what's happening.

2. **The LLM call is the LAST thing that happens, not the first.**
   Everything before Execute is deterministic, instant, and sets up success.

3. **Learn from every interaction.**
   Success → reinforce. Failure → adjust. Timeout → switch. This is the compounding.

4. **Fail fast, recover gracefully.**
   Circuit breakers prevent wasting time. Timeouts are per-complexity. Ctrl+C is clean.

5. **Right-size the payload.**
   SIMPLE gets 200 tokens. COMPLEX gets 10,000. Never send a cannon to kill a mosquito.

6. **No magic, no heuristic guessing.**
   Classification is rule-based and predictable. No "maybe this needs tools" uncertainty.

7. **Respect the user's choice.**
   Auto-switch only happens after EVIDENCE of failure (3+ attempts, reliability < 0.3).
   Always tell the user WHY a switch happened. Never silently degrade quality.

8. **Zero external dependencies.**
   Entire runtime uses Python stdlib only. No Redis, no databases, no pip installs.
   Consistent with the existing pyproject.toml `dependencies = []`.

9. **Backwards compatible.**
   Slash commands, /agent, /spec, daemon, jobs — all unchanged.
   The runtime replaces only the inner message-handling loop in chat.py.

10. **Testable in isolation.**
    Each module (classifier, self_model, reflect, runtime) is independently unit-testable
    with no network, no LLM, no filesystem (when mocked).

---

## Comparison: Cloud vs CLI

| Aspect | Cloud (81 modules) | CLI (4 new modules) |
|--------|-------------------|---------------------|
| Bootstrap | FORTRESS + SILENCE + credits + persona + CRDT + breakers + SelfModel + decay + WorldModel + predictions + concepts + goals | Load self_model.json + decay + circuit breaker + model selection |
| Plan | Intent classifier + FastPath + SelfModel summary + beliefs + concepts + goals + tool reliability | FastPathClassifier (rule-based) + payload construction |
| Execute | SIMPLE (3 rounds) or COMPLEX (multi-agent PlannerAgent → TaskGraph → ExecutorAgent × N) | SIMPLE/CONTEXT (single call) or COMPLEX (tool loop, max 3 rounds) |
| Reflect | HOT: valence + Bayesian. WARM: affect + world + goals. COLD: concepts + assessment + prompt evolution | HOT: valence + reliability. WARM: frustration + advice. COLD: health report. |
| Persist | CRDT + persona + SelfModel + WorldModel + concepts + goals + billing + audit | Atomic write self_model.json |
| Storage | Firebase RTDB + Storage + Redis + Firestore | Single JSON file |
| Transport | WebSocket + REST + Redis Streams | stdin/stdout |
| Concurrency | 10 + 50 queued executions | 1 (single user, single thread) |
| Learning speed | Same Bayesian formula | Same Bayesian formula |
| Recovery | OMEGA (fork-and-compare generations) | Auto-switch to reliable alternative |

The CLI version captures the **essence** — classification, learning, transparency, graceful failure —
without the infrastructure overhead of multi-tenant cloud operation.

---

*End of plan. Ready for implementation on request.*
