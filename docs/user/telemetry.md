# Telemetry

Kognisant records execution statistics locally for every interaction. This data helps you understand model performance, track token usage, monitor system health, and make informed decisions about model selection.

---

## Why Telemetry Matters

Without visibility into how the AI is performing, you are guessing. Telemetry answers questions like:

- Which model gives me the best results?
- How many tokens am I using per session?
- Is my local model getting slower over time?
- Why did the system switch models automatically?
- What is my overall success rate?

All telemetry data stays local on your machine. Nothing is sent externally.

---

## The /telemetry Command

### Summary view

```
/telemetry
```

Shows an aggregate summary of recent execution statistics:

```
Telemetry Summary (last 50 executions):

  Total executions:    50
  Success rate:        94%
  Avg response time:   7.8s
  Total tokens in:     142,000
  Total tokens out:    28,400

  Models used:
    gemma4:latest      38 calls | 95% success | avg 6.2s
    deepseek-chat      12 calls | 92% success | avg 12.1s

  Classifications:
    SIMPLE:   12 (24%)
    CONTEXT:  18 (36%)
    COMPLEX:  17 (34%)
    AUTONOMOUS: 3 (6%)

  Valence trend: Good (+22)
```

### Per-model deep dive

```
/telemetry gemma4:latest
```

Shows detailed statistics for a specific model:

```
Model: gemma4:latest

  Executions:       38
  Successes:        36
  Failures:         2
  Reliability:      0.93 (Bayesian)
  Avg response:     6.2s
  Token calibration: 1.08

  Failure breakdown:
    Timeout:   1
    Empty:     1
    Error:     0

  Response time distribution:
    <5s:   14 (37%)
    5-15s: 20 (53%)
    15-30s: 3 (8%)
    >30s:   1 (3%)

  Circuit breaker: CLOSED (healthy)
  Last failure: 2025-06-14T09:22:00Z
```

---

## What Is Recorded Per Execution

Every time you send a message and receive a response, a telemetry record is appended:

| Field | Description |
|:---|:---|
| `timestamp` | When the execution started (ISO-8601 UTC) |
| `model` | Which model handled the request |
| `classification` | SIMPLE, CONTEXT, COMPLEX, or AUTONOMOUS |
| `success` | Whether the execution completed without error |
| `response_time_ms` | Total wall-clock time in milliseconds |
| `tokens_in` | Estimated input tokens |
| `tokens_out` | Output tokens received |
| `tools_called` | Number of tool calls made |
| `tool_names` | List of tool names invoked |
| `valence_before` | Valence score before this execution |
| `valence_after` | Valence score after this execution |
| `error_type` | If failed: timeout, empty, api_error, etc. |
| `thinking_duration_ms` | Time spent in reasoning (if applicable) |
| `phase_times` | Breakdown of time per phase (bootstrap, plan, execute, reflect) |

---

## File Format

### Location

```
~/.kognisant_core/telemetry.jsonl
```

The file uses JSON Lines format (one JSON object per line). This makes it easy to append without reading/rewriting the entire file.

### Example record

```json
{"timestamp":"2025-06-15T14:30:22Z","model":"gemma4:latest","classification":"COMPLEX","success":true,"response_time_ms":18200,"tokens_in":2100,"tokens_out":420,"tools_called":3,"tool_names":["read_project_file","edit_project_file","create_project_file"],"valence_before":22,"valence_after":27,"thinking_duration_ms":12400}
```

### Rotation at 5MB

When `telemetry.jsonl` reaches 5MB, Kognisant rotates it:

1. The current file is renamed to `telemetry.jsonl.1`
2. A fresh `telemetry.jsonl` is created
3. Older rotated files (`.2`, `.3`, etc.) are not kept; only one backup is retained

This prevents unbounded growth while preserving recent history.

---

## Valence Tracking

Valence is a system-wide mood score ranging from -100 to +100. It reflects how well things have been going:

### How valence changes

| Event | Delta |
|:---|:---|
| Success, fast (<10s) | +5 |
| Success, moderate (10-30s) | +3 |
| Success, slow (>30s) | +1 |
| Timeout | -15 |
| Empty response | -10 |
| Generic error | -10 |
| User cancelled | -5 |
| Background pressure (failed jobs, stale world model) | up to -5 |

### Temporal decay

Valence decays 10% toward zero per calendar day of inactivity. If you do not use Kognisant for 3 days, a valence of +50 would decay to approximately +36.

### Interpreting valence

| Range | Status | Meaning |
|:---|:---|:---|
| +50 to +100 | Excellent | Consistent success, fast responses |
| +20 to +49 | Good | Mostly working well |
| 0 to +19 | Neutral | Mixed results |
| -20 to -1 | Declining | Some failures accumulating |
| -50 to -21 | Poor | Frequent issues, consider switching models |
| -100 to -51 | Critical | Persistent failures, intervention needed |

---

## Model Reliability Scores

Each model has a Bayesian reliability score:

```
reliability = (successes + 1) / (successes + failures + 2)
```

This formula (Laplace smoothing) starts at 0.5 and moves toward the true success rate as more data accumulates. It means:

- A new model starts at 0.5 (no opinion)
- After 10 successes and 0 failures: 0.92
- After 10 successes and 2 failures: 0.79
- After 5 successes and 5 failures: 0.5

Reliability is used by:
- Circuit breaker logic (auto-switch on low reliability)
- Agent model selection (prefer high-reliability models for workers)
- WARM reflection advisories (warning when reliability drops below 0.3)

---

## Token Usage Trends

Track your token consumption over time by examining the telemetry file:

```bash
# Count tokens used today
grep "$(date -u +%Y-%m-%d)" ~/.kognisant_core/telemetry.jsonl | \
  python3 -c "
import sys, json
records = [json.loads(l) for l in sys.stdin]
total_in = sum(r.get('tokens_in', 0) for r in records)
total_out = sum(r.get('tokens_out', 0) for r in records)
print(f'Today: {total_in:,} in / {total_out:,} out ({len(records)} executions)')
"
```

Or just use the `/telemetry` command for a formatted summary.

### Token calibration

Each model has a per-model token calibration factor that improves estimation accuracy over time:

```
calibration = calibration * 0.8 + (actual/estimated) * 0.2
```

The `📋` line uses this calibration to show more accurate token estimates. After 10-20 executions with a model, estimates become quite precise.

---

## Background Pressure

The valence system also accounts for background system health:

| Signal | Pressure |
|:---|:---|
| Average tool reliability below 50% | -2 |
| Failed daemon jobs (per job) | -1 |
| World model stale (>24h without update) | -1 |
| Goal acceptance rate below 30% | -1 |
| Combined maximum | -5 |

This means even successful executions can have slightly reduced valence gains if background signals indicate system stress.

---

## When to Check Telemetry

| Situation | What to look at |
|:---|:---|
| Responses feel slow | `/telemetry <model>` - check avg response time trend |
| Getting frequent errors | `/telemetry` - check success rate and failure breakdown |
| Choosing between models | Compare reliability scores and response times |
| System auto-switched models | Check circuit breaker state in per-model view |
| Cost tracking (cloud models) | Look at token totals combined with pricing info |
| Verifying a fix worked | Check if recent executions show improved success rate |

---

## Privacy

All telemetry data is:
- Stored locally only (`~/.kognisant_core/telemetry.jsonl`)
- Never transmitted anywhere
- Does not contain message content (only metadata)
- Under your full control (delete the file anytime)

The telemetry file records statistical metadata about executions, not the actual messages or responses. Your conversations remain in session history only.
