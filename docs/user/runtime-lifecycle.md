# Runtime Lifecycle

Every message you send in Kognisant goes through a 5-phase execution pipeline. Understanding these phases helps you interpret the status lines, debug slow responses, and know when to intervene.

---

## Why Know the Lifecycle?

The emoji status lines printed during every interaction are not decorative. They tell you exactly what is happening, how much context is being consumed, how long things took, and whether the system is healthy. Once you understand them, you can diagnose issues at a glance.

---

## The 5 Phases

```
User Message
    │
    ▼
┌─ Bootstrap ──── Model selection, capabilities, health check
│
├─ Plan ────────── Classify, build prompt, estimate tokens
│
├─ Execute ─────── API call, streaming, tool execution
│
├─ Reflect ─────── Validate outcome, update reliability
│
└─ Persist ─────── Save session, update memory atomically
```

---

## Phase 1: Bootstrap

**What happens:**
- Load the SelfModel (valence, reliability data, circuit breakers)
- Apply temporal decay (valence decays toward 0, frustration halves)
- Select the active model (check circuit breakers, possibly auto-switch)
- Scan capabilities (count skills, tools, jobs, specs)
- Health-check local models (ping Ollama/llama.cpp endpoint)

**Output line:**

```
⚡ gemma4:latest | valence: +22 | 8 skills, 4 tools
```

Breaking this down:
- `⚡` - Bootstrap phase indicator
- `gemma4:latest` - Active model name
- `valence: +22` - System mood score (-100 to +100). Green if positive, red if negative
- `8 skills, 4 tools` - Loaded capabilities count

**Special cases:**

First execution ever:
```
⚡ Welcome - first execution. Using gemma3:1b (configured default). No history yet.
```

Auto-switch (circuit breaker tripped):
```
⚡ Switching → llama-3.3-70b-versatile
  ⚠️  gemma3:1b circuit breaker OPEN; using llama-3.3-70b-versatile (reliability: 0.89)
```

Local model unreachable:
```
⚡ ⚠️  Ollama server not reachable at http://localhost:11434
```

---

## Phase 2: Plan

**What happens:**
- Classify the message into one of 4 categories
- Build the system prompt appropriate to the classification
- Set timeouts based on classification and model type
- Construct the API message array (system + history + user message)
- Attach tools (only for COMPLEX classification)
- Compute token breakdown estimates

**Classifications:**

| Type | Meaning | Context Loaded | Tools |
|:---|:---|:---|:---|
| SIMPLE | Quick factual answer | Minimal | No |
| CONTEXT | Needs project awareness | Memory + files | No |
| COMPLEX | Needs tools and full context | Everything | Yes |
| AUTONOMOUS | Multi-step, delegated to swarm | Agent dispatch | N/A |

**Output line:**

```
📋 COMPLEX → ~2,100 tokens input (sys: 800 + tools: 600 + hist: 400 + msg: 300)
```

Breaking this down:
- `📋` - Plan phase indicator
- `COMPLEX` - Classification result
- `~2,100 tokens input` - Estimated total input tokens
- `(sys: 800 + tools: 600 + hist: 400 + msg: 300)` - Token breakdown by source

For AUTONOMOUS:
```
📋 AUTONOMOUS → delegating to agent swarm
  Detected multi-step task requiring parallel execution
```

---

## Phase 3: Execute

**What happens:**
- Send the API request to the model
- Stream the response token by token
- Handle tool calls if the model requests them
- Display reasoning steps if the model produces thinking tokens
- Retry on retryable errors (up to 3 attempts)
- Escalate to swarm if dynamic escalation triggers

**Output during execution:**

Thinking display:
```
💭 Thinking...
  1. Analyze the current middleware structure.
  2. JWT verification needs to happen before route handlers.
💭 Thought for 8.3s
```

Tool call boxes:
```
  ┌─ Read auth/middleware.py ────────────────────────────────────────────┐
  │ ✓ 2ms | 3.2KB read                                                  │
  └──────────────────────────────────────────────────────────────────────┘
  ┌─ Accepted edits to auth/middleware.py ──────────────────────────────┐
  │ ✓ 4ms | 3 edits applied                                             │
  └──────────────────────────────────────────────────────────────────────┘
```

**Streaming:** The final text response streams to your terminal in real-time. You see words appear as the model generates them.

---

## Phase 4: Reflect

**What happens:**
- Run HOT reflection (every execution)
- Possibly run WARM reflection (every 3rd execution)
- Possibly run COLD reflection (every 20th execution)
- Update valence based on outcome
- Update model reliability (Bayesian)
- Update tool reliability (per-tool success tracking)
- Check for advisories (consecutive failures, low reliability)

**Reflection levels:**

| Level | Frequency | What it does |
|:---|:---|:---|
| HOT | Every turn | Update valence, model stats, tool stats |
| WARM | Every 3rd | Check for patterns (consecutive failures, low reliability warnings) |
| COLD | Every 20th | Full health report (success rates, response times, valence trend) |

**Output line:**

```
🔍 18.2s | 2,100 in > 420 out | valence: +27 (+5) | 3 tool(s)
```

Breaking this down:
- `🔍` - Reflect phase indicator
- `18.2s` - Total wall-clock time for this execution
- `2,100 in > 420 out` - Input tokens sent, output tokens received
- `valence: +27 (+5)` - New valence score and the delta from this execution
- `3 tool(s)` - Number of tool calls made

**WARM advisory (when triggered):**
```
  ⚠️  3 consecutive failures. Consider /model to switch.
```

**COLD health report (when triggered):**
```
  📊 Health Report:
  Total executions: 40
  Success rate: 92%
  Avg response time: 8.3s
    gemma4:latest: 35s/3f (rel: 0.92, avg: 7.1s)
    deepseek-chat: 5s/0f (rel: 0.86, avg: 12.4s)
  Valence trend: Good (+22)
```

---

## Phase 5: Persist

**What happens:**
- Save session history to `.kognisant/history/`
- Update `context.md` if significant changes were made
- Save SelfModel (valence, reliability) to `~/.kognisant_core/self_model.json`
- All writes are atomic (temp + fsync + rename)

**No output line** - Persist is silent unless there is an error.

If persist fails (disk full, permissions), the error is logged but does not affect your response. Your answer was already displayed during Execute.

---

## Timeouts

### By classification and model type

| Classification | Local Model | Cloud Model |
|:---|:---|:---|
| SIMPLE | 120s | 30s |
| CONTEXT | 180s | 60s |
| COMPLEX | 300s | 120s |

Local models get extended timeouts because they may need time to load into GPU memory (cold start) and generate tokens more slowly than cloud inference.

### Adaptive behavior

If a model consistently responds within a certain time range, the timeout is adequate. If it times out, the reflect phase records a -15 valence hit and increments the failure counter.

### What happens on timeout

1. The API call is cancelled
2. The reflect phase records a timeout event
3. The circuit breaker window is updated
4. You see an error in your terminal
5. The system does NOT retry timeouts (they are not retryable)

---

## Retry Strategy

Retryable errors get up to 3 attempts before failure:

### What is retryable:

- HTTP 429 (rate limited)
- HTTP 500, 502, 503 (server errors)
- Connection timeouts (different from response timeouts)
- Network errors (connection refused, DNS failure)

### What is NOT retryable:

- HTTP 401 (bad API key)
- HTTP 400 (malformed request)
- Response timeout (model too slow)
- Empty response (model returned nothing)
- JSON parse errors (model returned invalid format)

### Retry behavior:

1. First attempt fails with a retryable error
2. Wait briefly, then retry (attempt 2)
3. If attempt 2 fails, retry once more (attempt 3)
4. If attempt 3 fails, record the failure and show the error

There is no exponential backoff between retries. The goal is fast recovery for transient issues.

---

## Rollback on Failure

If execution fails (after retries are exhausted or on a non-retryable error):

- No files are written to disk
- No memory is updated
- Session history records the failed attempt (for debugging)
- Valence takes a hit (-10 to -15)
- The model's reliability score is updated

This means a failed execution never corrupts your project state. The worst case is lost time, never lost data.

---

## The Full Picture

Here is a complete execution flow for a COMPLEX message:

```
You > refactor the auth module to use JWT

⚡ gemma4:latest | valence: +22 | 8 skills, 4 tools
📋 COMPLEX → ~2,100 tokens input (sys: 800 + tools: 600 + hist: 400 + msg: 300)
💭 Thinking...
  1. Read the current auth module to understand the structure.
  2. The bcrypt-based session system needs to be replaced with JWT.
  3. I'll need to update the middleware, login route, and tests.
💭 Thought for 12.4s
  ┌─ Read auth/middleware.py ────────────────────────────────────────────┐
  │ ✓ 2ms | 3.2KB read                                                  │
  └──────────────────────────────────────────────────────────────────────┘
  ┌─ Accepted edits to auth/middleware.py ──────────────────────────────┐
  │ ✓ 4ms | 3 edits applied                                             │
  └──────────────────────────────────────────────────────────────────────┘
  ┌─ Created auth/jwt_utils.py ─────────────────────────────────────────┐
  │ ✓ 1ms | created (1.8KB)                                             │
  └──────────────────────────────────────────────────────────────────────┘

Kognisant >
Done. I've replaced the session-based auth with JWT:
- Created `auth/jwt_utils.py` with token generation and verification
- Updated middleware to validate Bearer tokens
- Preserved the existing user lookup logic

🔍 18.2s | 2,100 in > 420 out | valence: +27 (+5) | 3 tool(s)
```

Each line tells a story. Learn to read them, and you will never wonder what Kognisant is doing.
