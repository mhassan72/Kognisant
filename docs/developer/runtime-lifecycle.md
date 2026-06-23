# Runtime Lifecycle (5-Phase Cognitive Runtime)

Deep technical documentation of the runtime orchestrator that processes every
non-slash user message through a deterministic 5-phase pipeline.

## Why 5 Phases

The pipeline exists because a single LLM request hides too much complexity:
model selection depends on past reliability, token estimation affects timeout,
tool calls need looping, reflection updates cognitive state, and everything
needs persistence. Splitting these into explicit phases gives us:

1. Clear failure boundaries (errors in Plan never corrupt Persist)
2. Measurable timing per phase (exposed in telemetry)
3. Testable units (each phase is a pure-ish function of ExecutionContext)
4. Rollback semantics (message history checkpointing happens before Execute)

```
execute_message(user_message, messages, model_config, project_info, session_file)
│
├─ Phase 1: BOOTSTRAP ──── Load SelfModel, apply decay, select model, scan caps
│
├─ Phase 2: PLAN ────────── Classify message, build prompt, compute tokens
│
├─ Phase 3: EXECUTE ─────── Stream LLM, tool loop (max 3 rounds), retry (max 3)
│
├─ Phase 4: REFLECT ─────── HOT/WARM/COLD reflection, update valence, telemetry
│
├─ Phase 5: PERSIST ─────── Atomic write SelfModel to disk
│
└─ return ExecutionResult
```

### Why This Order

- Bootstrap before Plan: model selection may disable tools (persisted capability)
- Plan before Execute: classification determines timeout and tool availability
- Execute before Reflect: reflection needs outcome data (success, timing, tools)
- Reflect before Persist: persist writes the state that reflect just computed
- Persist is last: if it fails, the system recovers to the previous state on next run

## ExecutionContext Dataclass

The internal state object that flows through all 5 phases. Never exposed
outside `runtime.py`. Fields are grouped by the phase that writes them.

```python
@dataclass
class ExecutionContext:
    # --- Input (set once at construction) ---
    user_message: str          # The raw user input
    messages: list[dict]       # Mutable history list (owned by runtime during execution)
    model_config: dict         # Configured model (NEVER mutated)
    project_info: dict | None  # Project context or None
    session_file: str | None   # Session filename for persistence
    checkpoint_idx: int        # len(messages) at start - rollback target

    # --- Bootstrap output ---
    self_model: SelfModel      # Loaded and decayed cognitive state
    active_model: dict         # Shallow copy of model_config (may have flags added)
    auto_switched: bool        # True if circuit breaker triggered model switch
    switch_reason: str         # Human-readable switch reason
    capability_snapshot: dict  # Counts of skills, tools, jobs, etc.

    # --- Plan output ---
    classification: str        # SIMPLE | CONTEXT | COMPLEX | AUTONOMOUS
    system_prompt: str         # Built based on classification
    api_messages: list[dict]   # Windowed messages for API call
    tools: list[dict] | None   # Tool definitions (None for SIMPLE/CONTEXT)
    timeout: int               # Timeout in seconds (varies by classification + model type)
    token_breakdown: dict      # Per-component token estimates
    total_tokens_in: int       # Sum of token breakdown

    # --- Execute output ---
    success: bool
    response: str
    streamed: bool
    response_time: float
    total_tokens_out: int
    tool_calls_made: int
    tools_used: list[dict]     # [{"name": str, "success": bool, "duration": float}]
    error: str | None
    error_type: str | None     # "api_error" | "timeout" | "empty" | "cancelled"
    timed_out: bool
    cancelled: bool
    stalled: bool

    # --- Timing ---
    phase_times: dict          # {"bootstrap": ms, "plan": ms, "execute": ms, ...}
```

### Design Decision: Mutable Dataclass

We chose a mutable dataclass over a builder pattern because:
- Phases need to read AND write the same state
- The object never leaves the `execute_message` call stack
- No concurrency (single-threaded pipeline within one user message)
- Fields are logically grouped by phase, making it clear who writes what

## ExecutionResult Dataclass

Returned to `chat.py` after execution completes. This is the public interface.

```python
@dataclass
class ExecutionResult:
    success: bool
    response: str
    streamed: bool
    error: str | None
    classification: str
    model_used: str
    response_time: float
    tool_calls_made: int
    valence_delta: int
    timed_out: bool
    cancelled: bool
    tokens_in: int
    tokens_out: int
```

### Why a Separate Return Type

`chat.py` shouldn't see SelfModel, circuit breakers, or internal retry state.
ExecutionResult is a clean summary that chat.py uses to decide what to display
and whether to save the session.

## Phase 1: Bootstrap

```
_bootstrap(ctx):
│
├─ Load SelfModel from disk (safe defaults if absent/corrupt)
├─ Apply temporal decay (valence 10%/day toward 0, frustration halves/day)
├─ Select model via circuit breaker + reliability priority
│   ├─ Default model CB is CLOSED? -> use it
│   ├─ Default model CB is HALF_OPEN? -> test attempt
│   └─ Default model CB is OPEN?
│       ├─ Cooldown expired? -> transition to HALF_OPEN, use it
│       └─ Find best alternative (reliability > 0.5)
│           ├─ Found? -> auto-switch, set ctx.auto_switched = True
│           └─ Not found? -> use default anyway with warning
├─ Check persisted capabilities (tool_calling disabled by self-healing?)
├─ Scan environment capabilities (skills, tools, jobs counts)
├─ Health check for local models (Ollama GET /api/tags, llama.cpp GET /health)
│   └─ Not running? -> early exit with error, skip all remaining phases
└─ Print ⚡ bootstrap line
```

### Local Model Health Check

Pre-flight check completes in <2s. Prevents waiting 120s for a timeout when
Ollama isn't running:

```python
def _check_local_health(model_config: dict) -> tuple[bool, str]:
    if protocol == "ollama":
        health_url = api_base.replace("/api/chat", "").rstrip("/") + "/api/tags"
    elif protocol == "llama_cpp":
        health_url = api_base.replace("/v1/chat/completions", "").rstrip("/") + "/health"
    # GET with 2s timeout - fast fail
```

## Phase 2: Plan

```
_plan(ctx):
│
├─ classify(user_message) -> SIMPLE | CONTEXT | COMPLEX
│
├─ If COMPLEX: check _detect_autonomous()
│   └─ True? -> upgrade to AUTONOMOUS
│
├─ Build system prompt (varies by tier):
│   ├─ SIMPLE: minimal "respond naturally" (~50 tokens)
│   ├─ CONTEXT: project files + context.md (~1500 tokens)
│   └─ COMPLEX: full prompt + memory + guidelines + skills (~2000 tokens)
│
├─ Set timeout:
│   ├─ Remote:  SIMPLE=30s, CONTEXT=60s, COMPLEX=120s
│   └─ Local:   SIMPLE=120s, CONTEXT=180s, COMPLEX=300s
│
├─ Build api_messages with window:
│   ├─ SIMPLE: last assistant msg only
│   ├─ CONTEXT: last 10 messages
│   └─ COMPLEX: last 20 messages (tool results >500 chars pruned)
│
├─ Set tools (only for COMPLEX with tool_calling not disabled)
│
├─ Compute token breakdown:
│   { system, tools, history, user_message, total }
│
└─ Print 📋 plan line (classification + token summary)
```

### Message Window Pruning (COMPLEX)

Old tool results are summarized to save context tokens:

```python
if m.get("role") == "tool" and len(m.get("content", "")) > 500:
    pruned.append({
        "role": "tool",
        "tool_call_id": m.get("tool_call_id", ""),
        "name": tool_name,
        "content": f"[Previously: {tool_name} returned {char_count} chars. Re-read if needed.]",
    })
```

This is critical for local models with smaller context windows. A single
`read_project_file` result can be 10KB+, but on the second round the model
usually doesn't need the full content.

## Phase 3: Execute

The most complex phase. Handles streaming, thinking tokens, tool loops,
retries, and escalation.

```
_execute(ctx):
│
├─ Append user message to messages + api_messages
├─ Save session (checkpoint)
│
├─ FOR attempt IN 1..3:
│   │
│   ├─ Calculate timeout (escalates: 1x, 2x, 2.5x for local / 1x for remote)
│   ├─ Show retry indicator if attempt > 1
│   │
│   ├─ FOR round IN 1..3 (tool loop):
│   │   │
│   │   ├─ Build payload (model, messages, tools, think flag)
│   │   ├─ Start Spinner (with timeout + elapsed display)
│   │   │
│   │   ├─ Stream via query_model_api_stream():
│   │   │   ├─ "phase:connected" -> update spinner sub-state
│   │   │   ├─ "thinking" -> stop spinner, show dim gray thinking tokens
│   │   │   ├─ "content" -> stop spinner, print "Kognisant >", stream content
│   │   │   ├─ "tool_calls" -> collect tool call list
│   │   │   └─ "done" -> get final assistant message
│   │   │
│   │   ├─ Save thinking to session file
│   │   ├─ Handle token calibration from _usage
│   │   ├─ Append assistant message to messages + session
│   │   │
│   │   ├─ Tool calls present?
│   │   │   ├─ YES -> _execute_tools() -> append results -> continue loop
│   │   │   └─ NO -> break (attempt done)
│   │   │
│   │   └─ CATCH KognisantAPIError:
│   │       ├─ Retryable? -> break tool loop, continue attempt loop
│   │       └─ Fatal? -> _handle_api_error(), return
│   │
│   ├─ Attempt succeeded with content? -> break retry loop
│   ├─ Attempt succeeded but empty?
│   │   ├─ Attempts remain? -> rollback, retry
│   │   └─ All exhausted? -> error "empty after 3 attempts"
│   └─ Retryable error? -> continue to next attempt
│
├─ Post-execution:
│   ├─ Persist reasoning capability detection
│   └─ Post-exhaustion escalation (3 rounds, no content -> swarm)
│
└─ CATCH KeyboardInterrupt -> ctx.cancelled = True, rollback
```

### Spinner Sub-States

The spinner message updates as the execution progresses:

```
State 1: "⚙️  gemma4:latest - loading model..."        (local) or "connecting..." (remote)
State 2: "⚙️  gemma4:latest - waiting for response..."  (after "connected" event)
State 3: "⚙️  gemma4:latest - thinking..."              (remote models, before thinking tokens)
```

The spinner includes elapsed time and the configured timeout:

```
⚙️  gemma4:latest - waiting for response... (12.3s / 300s)
```

When thinking tokens arrive, the spinner stops entirely and is replaced by
the dim gray thinking display. The spinner never resumes after thinking starts.

### Retry Strategy

```
Attempt 1: Normal timeout (e.g. 120s for COMPLEX remote)
Attempt 2: 2x timeout (240s) + 2s delay for remote rate limiting
Attempt 3: Extended timeout (300s local, 120s remote) + "extended timeout" indicator
```

Between attempts, the runtime:
1. Rolls back messages to checkpoint (removes failed assistant/tool messages)
2. Delays 2s for remote models (respects rate limiting)
3. Resets round counter and response state

### Retryable vs Fatal Errors

```python
def _is_retryable_error(error_str: str) -> bool:
    # Retryable: timeout, stall, generic connection
    # NOT retryable: 401 (auth), 402 (payment), 429 (rate limit), tool errors
```

401/402/429 are NOT retried because they won't resolve on retry. The user needs
to fix their key or wait. Tool detection errors (400 with "tool" or "function")
trigger self-healing instead of retry.

### AUTONOMOUS Escalation from Execute

Two paths lead to agent swarm:

1. **Pre-detection** (in Plan phase): `_detect_autonomous()` returns True
   - Immediate escalation, no Execute phase at all
2. **Post-exhaustion** (in Execute phase): 3 rounds used, no content produced
   - Rollback the failed attempt
   - Show "auto-escalating to agent swarm" message
   - Call `_escalate_to_swarm(ctx)`

## Tool Box Rendering

Tool execution is visualized with animated terminal boxes using Unicode
box-drawing characters and 24-bit ANSI colors.

### Box Format

```
  ┌─ Reading src/main.py ─────────────────────────────────────────────────┐
  │ ✓ 320ms | 4.2KB read                                                  │
  └────────────────────────────────────────────────────────────────────────┘
```

### Color Scheme (24-bit ANSI)

```python
_GRAY   = "\033[38;2;149;165;166m"   # Progress/animation
_ORANGE = "\033[38;2;243;156;18m"    # Alternating animation frame
_GREEN  = "\033[38;2;39;174;96m"     # Success
_RED    = "\033[38;2;231;76;60m"     # Failure
```

### Animation Thread

For tools taking >=150ms, a daemon thread animates the box in place:

```
Animation cycle (150ms per frame):
  Frame 0: ◐ (gray)
  Frame 1: ◓ (gray)
  Frame 2: ◑ (orange)
  Frame 3: ◒ (orange)
  ... repeats with elapsed time updating
```

The thread uses ANSI escape sequences to overwrite the 3-line box:
```python
sys.stdout.write(f"\033[3A\033[2K{header}\n\033[2K{content}\n\033[2K{bottom}\n")
```

- `\033[3A` - move cursor up 3 lines
- `\033[2K` - clear entire current line

When the tool completes, `done_event.set()` stops the thread, and the main
thread redraws the final box (green for success, red for failure).

### Box Width Adaptation

```python
def _get_box_width() -> int:
    cols = shutil.get_terminal_size().columns
    return max(50, min(cols - 4, 76))
```

Adapts to terminal width with a floor of 50 and ceiling of 76. The -4 margin
accounts for the 2-space indent and breathing room.

### Non-TTY Fallback

When `sys.stdout.isatty()` returns False (piped output, CI, logging):

```python
if not is_tty:
    print(f"  [{status_icon}] {final_header} - {duration_ms:.0f}ms | {summary}")
```

No ANSI codes, no animation, no cursor manipulation. Just a plain text line
per tool call. The animation thread is never started.

## Error Handling and Rollback

### Rollback Mechanics

```python
def _rollback(ctx: ExecutionContext) -> None:
    while len(ctx.messages) > ctx.checkpoint_idx:
        ctx.messages.pop()
    _save_session_safe(ctx)
```

`checkpoint_idx` is set to `len(messages)` at the start of `execute_message`.
Rollback removes everything added during execution. This is used for:

- Retry (remove failed attempt's messages before trying again)
- Cancellation (Ctrl+C - don't leave half-formed messages in history)
- Post-exhaustion escalation (remove tool loop messages before delegating to swarm)

### Self-Healing (Tool Detection)

When an API returns 400 with "tool" or "function" in the error:

```
1. Disable tool_calling for this model in self_model (persisted!)
2. Set ctx.tools = None
3. Rollback messages
4. Retry with _execute_fallback (non-streaming, no tools)
5. On next execution, Bootstrap reads the persisted capability and skips tools
```

This means a single failure teaches the system permanently. The model will
never be offered tools again unless the user resets capabilities.

## Phase 4: Reflect

See [reflect-engine.md](reflect-engine.md) for the full reflection system.
The runtime calls it here and prints the output:

```
🔍 3.2s | 1450 in -> 380 out | valence: +15 (+5) | 2 tool(s)
```

## Phase 5: Persist

```python
def _persist(ctx: ExecutionContext) -> None:
    try:
        SelfModelEngine.save(ctx.self_model)
    except Exception:
        pass  # Never interrupt execution for persist failure
```

Uses the atomic write pattern (tmp + fsync + rename) from SelfModelEngine.
Wrapped in try/except because a persist failure (disk full, permissions) should
not prevent the user from seeing their response.

## Cross-References

- [fast-path-classifier.md](fast-path-classifier.md) - Classification used in Plan phase
- [self-model-engine.md](self-model-engine.md) - Bootstrap loads/saves this
- [reflect-engine.md](reflect-engine.md) - Reflect phase implementation
- [telemetry-system.md](telemetry-system.md) - Records appended in Reflect
- [thinking-and-reasoning.md](thinking-and-reasoning.md) - Thinking token handling in Execute
- [agent-escalation.md](agent-escalation.md) - AUTONOMOUS detection and swarm dispatch
