# Reflect Engine

Pure-computation reflection that runs after every execution to update
cognitive state. All operations complete in <10ms with zero I/O.

## Why Compute-Only

The reflect engine has a hard constraint: no LLM calls, no network requests,
no disk reads. This exists because:

1. Reflect runs on EVERY execution, including timeouts and cancellations
2. If reflect itself could fail (network error, disk full), we'd need
   reflect-on-reflect error handling (infinite regress)
3. <10ms budget means the user never perceives reflection delay
4. The data it needs (SelfModel, execution outcomes) is already in memory

The engine reads from `SelfModel` (passed by reference) and writes to it
directly. The runtime's Persist phase handles actually writing to disk.

## HOT / WARM / COLD Tier Design

Reflection is tiered by frequency to balance information density:

```
HOT  ─── every execution ──── update valence, reliability, failures
WARM ─── every 3rd execution ── check for problem patterns, emit advisories
COLD ─── every 20th execution ─ full health report with stats
```

### Why These Frequencies

- HOT (every): valence must be current for model selection decisions.
  Reliability updates must be immediate (circuit breaker depends on them).
- WARM (every 3rd): advisory messages ("3 failures, try /model") would be
  annoying every time. Every 3rd is frequent enough to catch problems,
  rare enough to not spam.
- COLD (every 20th): a full health report with per-model breakdowns is
  information-dense. Showing it every execution would overwhelm. Every 20th
  (~every few minutes of active use) gives a natural checkpoint.

### Frequency Checks

```python
def should_run_warm(total_executions: int) -> bool:
    return total_executions > 0 and total_executions % 3 == 0

def should_run_cold(total_executions: int) -> bool:
    return total_executions > 0 and total_executions % 20 == 0
```

Both use modulo on total_executions (lifetime counter). On execution 60,
both WARM and COLD fire (60 % 3 == 0 and 60 % 20 == 0).

## HOT Reflect - Valence Delta Rules

```python
def reflect_hot(self_model, *, success, response_time, timed_out, empty,
                cancelled, error, tools_used, model_name, background_pressure):
```

### Outcome-Based Delta

| Outcome | Delta | Rationale |
|---------|-------|-----------|
| Timeout | -15 | Most frustrating - user waited full duration for nothing |
| Empty response | -10 | Model responded but said nothing useful |
| Cancelled (Ctrl+C) | -5 | User chose to abort - mild negative |
| Generic error (API) | -10 | System failed, not user's fault |
| Success, fast (<10s) | +5 | Best case - quick useful response |
| Success, moderate (10-30s) | +3 | Good but user noticed the wait |
| Success, slow (>30s) | +1 | Succeeded but barely - the wait hurt |
| Other failure | -10 | Catch-all for unclassified failures |

### Why These Specific Numbers

The asymmetry is intentional: failures hurt more than successes help.
A single timeout (-15) requires three fast successes (+5 each) to recover.
This matches user psychology: one bad experience weighs more than one good one.

The slow success (+1) barely registers because a 40s wait for a response is
borderline frustrating even when it works. The system should learn to prefer
faster models.

### Background Pressure Addition

```python
clamped_pressure = max(background_pressure, -5)
if clamped_pressure < 0:
    valence_delta += clamped_pressure
```

The cap at -5 prevents background issues from dominating. Even with 10 failed
jobs and all tools broken, background pressure only contributes -5 per execution.
The primary signal remains the actual execution outcome.

### Additional HOT Updates

After computing valence delta, HOT also:

1. Updates `self_model.valence` (clamped [-100, +100])
2. Resets or increments `consecutive_failures`
3. Updates `model_reliability[name]` (Bayesian formula)
4. Updates rolling average response time
5. Updates `tool_reliability[name]` for each tool used
6. Increments `total_executions`

```python
# Model reliability update
mr.attempts += 1
if success:
    mr.successes += 1
else:
    mr.failures += 1
mr.reliability = (mr.successes + 1) / (mr.successes + mr.failures + 2)

# Rolling average response time
if mr.attempts == 1:
    mr.avg_response_time = response_time
else:
    mr.avg_response_time = (mr.avg_response_time * (mr.attempts - 1) + response_time) / mr.attempts
```

Note: the response time uses a simple cumulative average (not EMA) because we
want old data to still count. A model that was slow 20 executions ago is still
relevant for selection decisions.

## WARM Advisories

Runs every 3rd execution. Checks for patterns that suggest the user should
take action.

```python
def reflect_warm(self_model) -> list[str]:
```

### Advisory Conditions

| Condition | Advisory | Why |
|-----------|----------|-----|
| `consecutive_failures >= 3` | "3 consecutive failures. Consider /model to switch." | 3 in a row suggests a systemic problem, not a fluke |
| Model with `attempts >= 5` and `reliability < 0.3` | "{model} has low reliability (30%)" | 5 attempts is enough data, 0.3 is very unreliable |

### Why 3 Consecutive Failures

A single failure is normal (network blip, timeout). Two failures might be bad
luck. Three in a row is a pattern. By this point the circuit breaker may have
already tripped (5 failures in 30s), but WARM catches slower failure patterns
(e.g., one timeout per minute over 3 minutes).

### Why Reliability < 0.3

With Bayesian smoothing: `(s+1)/(s+f+2) < 0.3` means roughly 2x more failures
than successes. At 5+ attempts, this is statistically meaningful:
- 1s/4f: (2)/(7) = 0.29 - genuinely broken
- 2s/5f: (3)/(9) = 0.33 - borderline, won't trigger
- 0s/5f: (1)/(7) = 0.14 - definitely broken

## COLD Health Report

Runs every 20th execution. Produces a multi-line summary for the terminal.

```python
def reflect_cold(self_model) -> list[str]:
```

### Report Format

```
  -- Health Report --
  Total executions: 47
  Success rate: 82%
  Avg response time: 4.2s
    gemma4:latest: 30s/5f (rel: 86%, avg: 3.1s)
    gpt-4o: 12s/3f (rel: 81%, avg: 6.8s)
  Valence trend: Good (+22)
```

### Data Sources

| Line | Source |
|------|--------|
| Total executions | `self_model.total_executions` |
| Success rate | Sum of all model successes / (successes + failures) |
| Avg response time | Mean of `avg_response_time` across all models |
| Per-model breakdown | `model_reliability[name]` fields |
| Valence trend | Categorized from `self_model.valence` |

### Valence Trend Categories

```python
if valence >= 50:   trend = "Excellent"
elif valence >= 20: trend = "Good"
elif valence >= 0:  trend = "Neutral"
elif valence >= -20: trend = "Declining"
elif valence >= -50: trend = "Poor"
else:               trend = "Critical"
```

These are human-readable labels for the raw number. "Critical" at -50 or below
indicates sustained failures over many executions.

## Integration with Runtime

The runtime calls reflection in Phase 4:

```python
def _reflect(ctx):
    # HOT (always)
    valence_delta = reflect_hot(ctx.self_model, ...)

    # Update circuit breaker
    if success: cb_record_success(cb)
    elif error: cb_record_failure(cb)

    # Update timestamp
    ctx.self_model.last_execution_at = now_iso()

    # Print 🔍 line
    print(f"🔍 {time}s | {in} in -> {out} out | valence: {v} ({delta})")

    # WARM (every 3rd)
    if should_run_warm(total):
        advisories = reflect_warm(ctx.self_model)
        for a in advisories:
            print(f"  ⚠️  {a}")

    # COLD (every 20th)
    if should_run_cold(total):
        report = reflect_cold(ctx.self_model)
        print("  -- Health Report --")
        for line in report:
            print(f"  {line}")

    # Append telemetry record
    append_telemetry({...full execution record...})
```

Note that telemetry recording happens inside the reflect phase but is
technically I/O (file append). This is acceptable because:
1. `append_telemetry` wraps everything in try/except and never raises
2. The JSONL append is a single syscall (no lock contention)
3. If it fails, execution still succeeds

## Cross-References

- [runtime-lifecycle.md](runtime-lifecycle.md) - Phase 4 calls this engine
- [self-model-engine.md](self-model-engine.md) - SelfModel structure and persistence
- [telemetry-system.md](telemetry-system.md) - Records appended during reflect
