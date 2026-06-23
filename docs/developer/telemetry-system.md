# Telemetry System

Per-execution recording to `~/.kognisant_core/telemetry.jsonl` with rotation,
aggregation, and formatted terminal output.

## JSONL Format Choice

### Why Not SQLite

- Zero dependencies (no `sqlite3` import required at module level)
- Append-only is the dominant access pattern (one write per execution)
- JSONL is human-readable with standard tools (`tail -f`, `jq`)
- No schema migrations needed (new fields are just added to records)
- Crash-safe: a partial line at EOF is just skipped on read
- No file locking needed (single-writer: the runtime process)

### Why Not Plain JSON

A single JSON array would require:
- Loading the entire file to append one record
- Writing the entire file back (O(n) instead of O(1))
- Corruption on crash (partial write invalidates entire array)

### Why Append-Only

```python
def append_telemetry(record: dict) -> None:
    line = json.dumps(record, default=str) + "\n"
    with open(TELEMETRY_FILE, "a", encoding="utf-8") as f:
        f.write(line)
```

Each execution appends exactly one JSON line. The `"a"` mode open means:
- No read of existing content
- Kernel-level append atomicity on most filesystems (writes < PIPE_BUF)
- If the process crashes mid-write, only that one line is corrupt

### Error Suppression

```python
def append_telemetry(record: dict) -> None:
    try:
        os.makedirs(TELEMETRY_DIR, exist_ok=True)
        rotate_if_needed()
        ...
    except Exception as e:
        logger.warning(f"Failed to write telemetry: {e}")
```

Telemetry never raises. If the disk is full or permissions are wrong, execution
continues normally. Telemetry is observability, not functionality.

## Rotation Strategy

```
Trigger:   file size > 5MB (MAX_FILE_SIZE = 5 * 1024 * 1024)
Action:    rename telemetry.jsonl -> telemetry.1.jsonl (overwrite old backup)
Result:    at most 2 files, ~10MB total disk usage maximum
```

```python
def rotate_if_needed() -> None:
    if os.path.exists(TELEMETRY_FILE):
        size = os.path.getsize(TELEMETRY_FILE)
        if size > MAX_FILE_SIZE:
            if os.path.exists(TELEMETRY_BACKUP):
                os.remove(TELEMETRY_BACKUP)
            os.rename(TELEMETRY_FILE, TELEMETRY_BACKUP)
```

### Why 5MB and 1 Backup

- 5MB holds roughly 5,000-10,000 execution records (depending on tool usage)
- That's weeks to months of typical usage
- 1 backup means ~10MB max disk usage total
- The backup exists so that rotation doesn't lose ALL history at once
- `/telemetry` reads only from the primary file (not backup)

### Why Not Compress

Compression would require decompression for reads. Since `/telemetry` reads
the last 50 records (tail of file), we need raw text access. The 10MB cap
is small enough that compression gains don't justify the complexity.

## Per-Execution Record Schema

Every execution appends one record with these fields:

```json
{
  "timestamp": "2025-06-12T10:30:45.123456+00:00",
  "project": "cli-kognisant",
  "classification": "COMPLEX",
  "model": "gemma4:latest",
  "provider": "Ollama (Local)",
  "auto_switched": false,
  "tokens_in": 1450,
  "tokens_out": 380,
  "token_breakdown": {
    "system": 520,
    "tools": 680,
    "history": 150,
    "user_message": 100,
    "total": 1450
  },
  "response_time_ms": 3200.5,
  "phase_times_ms": {
    "bootstrap": 12.3,
    "plan": 2.1,
    "execute": 3180.0,
    "reflect": 1.5,
    "persist": 4.6
  },
  "tool_calls": [
    {"name": "read_project_file", "success": true, "duration": 0.012},
    {"name": "edit_project_file", "success": true, "duration": 0.045}
  ],
  "success": true,
  "error": null,
  "timed_out": false,
  "cancelled": false,
  "valence_before": 10,
  "valence_after": 15,
  "valence_delta": 5,
  "model_reliability_after": 0.857,
  "circuit_breaker_state": "closed"
}
```

### Field Notes

- `phase_times_ms`: allows identifying bottlenecks (bootstrap slow? persist slow?)
- `token_breakdown`: shows where context budget goes (useful for optimization)
- `tool_calls`: per-tool timing, not just count (slow tools vs fast tools)
- `valence_before/after/delta`: full audit trail for valence changes
- `circuit_breaker_state`: snapshot at reflect time (for debugging model switches)
- `auto_switched`: true when the system chose a different model from default

## Aggregation Algorithms

`aggregate_telemetry(records)` computes summary statistics from a list of records.

### Per-Model Breakdown

```python
per_model[model] = {
    "calls": count,
    "success_rate": successes / calls,
    "avg_time": total_time / calls,
    "tokens_in": sum,
    "tokens_out": sum,
}
```

### Per-Classification Breakdown

```python
per_classification[cls] = {
    "count": count,
    "avg_tokens": total_tokens / count,  # in + out combined
    "avg_time": total_time / count,
}
```

Useful for understanding resource allocation: are COMPLEX queries dominating
token usage? Are SIMPLE queries actually fast?

### Tool Usage Breakdown

```python
tool_usage[name] = {
    "calls": count,
    "success_rate": successes / calls,
}
```

Low success rate on a specific tool indicates a systemic issue (bad tool
implementation, permission problems, etc.)

### Valence Trend

```python
valence_trend = {
    "first": records[0].valence_after,
    "last": records[-1].valence_after,
    "delta": last - first,
}
```

Simple first-to-last comparison across the window. Shows whether things are
getting better or worse overall.

## Token Estimation Heuristic

```python
def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return len(text) // 4
```

### Why len // 4

- Average English word is ~5 characters
- Average token is ~4 characters (for most LLM tokenizers)
- `len(text) // 4` is a fast approximation with no dependencies
- No need for tiktoken, sentencepiece, or any tokenizer library
- Works for all protocols (Ollama, OpenAI, llama.cpp)

### Accuracy

Typical error range: +/-20% for English prose. Code tends to be slightly
over-estimated (tokens are shorter for common keywords), but the estimate
is "good enough" for:
- Choosing context window sizes
- Displaying token counts to the user
- Detecting when a response is abnormally large

### Calibration

When the API returns actual token counts (`_usage.prompt_tokens`), the runtime
feeds this back through `SelfModelEngine.update_token_calibration()`:

```python
ratio = actual / estimated
rel.token_calibration = rel.token_calibration * 0.8 + ratio * 0.2
```

This per-model correction factor could be applied to future estimates but is
not yet used in display. It's stored for future optimization.

## /telemetry Command Output

`format_telemetry_summary(records)` produces the display for `/telemetry`:

```
═══ Telemetry Summary (last 50 executions) ═══

  Total: 50  |  Success: 43 (86%)
  Avg response time: 4200ms
  Tokens: 72,500 in  |  19,300 out

  Models:
    gemma4:latest: 35 calls, 89% success, 3100ms avg
    gpt-4o: 15 calls, 80% success, 6800ms avg

  Classifications:
    SIMPLE: 12 calls, ~200 tokens avg, 1200ms avg
    CONTEXT: 18 calls, ~800 tokens avg, 2400ms avg
    COMPLEX: 20 calls, ~2100 tokens avg, 6500ms avg

  Tools:
    read_project_file: 45 calls, 100% success
    edit_project_file: 18 calls, 94% success
    shell_execution: 8 calls, 75% success

  Valence trend: +5 -> +18 (+13)
```

### /telemetry \<model\> Deep Dive

`format_model_telemetry(records, model_name)` provides per-model details:

```
═══ Model: gemma4:latest ═══

  Total calls: 35
  Success rate: 89% (31/35)
  Reliability: 0.89

  Response time:
    Average: 3100ms
    Fastest: 800ms
    Slowest: 28000ms

  Tokens:
    Avg in: 1200  |  Avg out: 380
    Total in: 42,000  |  Total out: 13,300

  Issues:
    Timeouts: 2
    Empty responses: 1

  Circuit breaker: closed
```

## Cross-References

- [runtime-lifecycle.md](runtime-lifecycle.md) - Reflect phase appends records
- [reflect-engine.md](reflect-engine.md) - Builds the telemetry record
- [self-model-engine.md](self-model-engine.md) - Token calibration stored here
