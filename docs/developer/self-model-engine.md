# Self-Model Engine (Cognitive State Persistence)

The self-model is Kognisant's persistent memory of how well things are going.
It tracks per-model reliability, per-tool success rates, circuit breaker states,
and an overall "valence" (mood) that influences model selection and user messaging.

Stored at `~/.kognisant_core/self_model.json`. Survives across sessions, projects,
and reboots.

## SelfModel Dataclass

```python
@dataclass
class SelfModel:
    version: int = 1                              # Schema version for forward-compat
    valence: int = 0                              # Overall mood [-100, +100]
    frustration: float = 0.0                      # Accumulated frustration (decays)
    total_executions: int = 0                     # Lifetime execution count
    consecutive_failures: int = 0                 # Resets to 0 on success
    last_execution_at: str | None = None          # ISO-8601 UTC timestamp
    model_reliability: dict[str, ModelReliability] = {}  # Per-model stats
    tool_reliability: dict[str, ToolReliability] = {}    # Per-tool stats
    circuit_breakers: dict[str, CircuitBreakerState] = {} # Per-model CB state
```

### ModelReliability (per model)

```python
@dataclass
class ModelReliability:
    successes: int = 0
    failures: int = 0
    reliability: float = 0.5       # Bayesian: (s+1)/(s+f+2)
    attempts: int = 0
    avg_response_time: float = 0.0 # Rolling average (seconds)
    last_success_at: str | None = None
    last_failure_at: str | None = None
    token_calibration: float = 1.0 # Correction factor for token estimates
    capabilities: dict = {"tool_calling": True}  # Learned capabilities
```

### ToolReliability (per tool)

```python
@dataclass
class ToolReliability:
    successes: int = 0
    failures: int = 0
    reliability: float = 0.5  # Bayesian: (s+1)/(s+f+2)
```

### CircuitBreakerState (per model)

```python
@dataclass
class CircuitBreakerState:
    state: str = "closed"              # "closed" | "open" | "half_open"
    failures_in_window: int = 0
    window_start: str | None = None    # ISO-8601
    open_until: str | None = None      # ISO-8601
```

## Atomic Write Pattern

Self-model persistence uses the same crash-safe pattern as the job queue:

```
SelfModelEngine.save(model):
│
├─ 1. os.makedirs(directory, exist_ok=True)
├─ 2. Serialize to JSON bytes
├─ 3. tempfile.mkstemp(dir=directory, suffix=".tmp")
├─ 4. os.write(fd, json_bytes)
├─ 5. os.fsync(fd)           # Data on physical media
├─ 6. os.close(fd)
├─ 7. os.replace(tmp_path, target_path)  # Atomic rename
│
├─ ON FAILURE:
│   ├─ Close fd if still open
│   └─ os.unlink(tmp_path) if it exists
│
└─ END
```

### Why Atomic Writes Here

The self-model is updated after every execution. If the user hits Ctrl+C
during a write, a half-written JSON file would cause the next load to fall back
to defaults, losing all reliability history. The atomic rename ensures the file
is either the old version or the new version, never partial.

### Load with Safe Fallback

```python
@staticmethod
def load(path=None) -> SelfModel:
    if not os.path.exists(path):
        return SelfModel()           # Fresh start
    try:
        data = json.load(open(path))
    except (json.JSONDecodeError, OSError):
        return SelfModel()           # Corrupted? Start fresh
    return SelfModelEngine._dict_to_model(data)
```

No backup recovery here (unlike job queue). The self-model is reconstructible
from telemetry if needed, and losing it only means losing learned reliability
data. Not worth the complexity of backup management.

## Bayesian Reliability Formula

```
reliability = (successes + 1) / (successes + failures + 2)
```

This is the Laplace-smoothed estimator (a.k.a. "rule of succession").

### Why Bayesian

```
Traditional:  success_rate = successes / total
  Problem:    0/0 = undefined, 1/1 = 100% (overconfident)

Bayesian:     reliability = (s+1) / (s+f+2)
  Cold start: (0+1)/(0+0+2) = 0.5 (neutral, not overconfident)
  After 1 success: (1+1)/(1+0+2) = 0.667
  After 1 failure: (0+1)/(0+1+2) = 0.333
  After 10s/0f:    (10+1)/(10+0+2) = 0.917 (converges toward truth)
  After 10s/10f:   (10+1)/(10+10+2) = 0.500
```

Properties that matter for us:
- **Cold start**: new model starts at 0.5, not 0.0 or 1.0
- **Convergence**: with more data, approaches true success rate
- **Never extreme**: can't reach 0.0 or 1.0 (always allows recovery)
- **Handles asymmetric data**: 3 successes + 0 failures = 0.8, not 1.0

The "+1" and "+2" represent a weak prior belief that models work ~50% of the time.
With enough evidence, the prior is overwhelmed by actual data.

## Valence System

Valence is an integer in [-100, +100] representing overall system health.
It influences user messaging and advisory triggers.

### Valence Delta Rules

```
Outcome-based:
  Timeout:            -15
  Empty response:     -10
  Cancelled (Ctrl+C): -5
  Generic error:      -10
  Success (fast, <10s): +5
  Success (moderate, <30s): +3
  Success (slow, >30s): +1
  Other failure:      -10

+ Background pressure (max -5, see below)

Applied: valence = clamp(valence + delta, -100, +100)
```

### Temporal Decay

Applied once at the start of each execution (Bootstrap phase):

```
days_elapsed = seconds_since(last_execution_at) / 86400

# Valence: decays 10% toward 0 per day
valence = int(valence * (0.9 ^ days_elapsed))

# Frustration: halves every 24 hours
frustration = frustration * (0.5 ^ days_elapsed)
```

Why decay? If the user hasn't used the system for a week, past frustration
shouldn't still dominate. The system "forgets" bad experiences over time,
matching human expectations.

### Background Signal Pressure

Computed by `SelfModelEngine.compute_background_signals()`:

```
Signal                          | Pressure
────────────────────────────────┼─────────
Avg tool reliability < 0.5      | -2
Failed jobs present             | -1 per job
World model stale (>24h)        | -1
Goal acceptance rate < 0.3      | -1
(Reserved for future signals)   |
────────────────────────────────┼─────────
Maximum combined                | -5 (capped)
```

Background pressure is added to the outcome-based delta in HOT reflect.
It provides a "gravity" that pulls valence down when systemic issues accumulate,
even if individual executions succeed.

## Circuit Breaker State Machine

Per-model circuit breaker that prevents repeated calls to a failing endpoint.

```
                  ┌───────────────────────────────────────┐
                  │                                       │
                  ▼                                       │
            ┌──────────┐                                  │
            │  CLOSED  │ <─── normal operation            │
            └────┬─────┘                                  │
                 │                                        │
                 │ 5 failures within 30s window           │
                 ▼                                        │
            ┌──────────┐                                  │
            │   OPEN   │ ─── rejects all attempts         │
            └────┬─────┘                                  │
                 │                                        │
                 │ 30s cooldown expires                   │
                 ▼                                        │
            ┌──────────┐                                  │
            │ HALF_OPEN│ ─── allows exactly 1 test        │
            └────┬─────┘                                  │
                 │                                        │
         ┌───────┴───────┐                                │
         │               │                                │
    test succeeds   test fails                            │
         │               │                                │
         ▼               ▼                                │
    -> CLOSED       -> OPEN (restart cooldown) ───────────┘
```

### Constants

```python
_CB_FAILURE_THRESHOLD = 5    # failures to trip
_CB_WINDOW_SECONDS = 30.0    # window for counting failures
_CB_COOLDOWN_SECONDS = 30.0  # how long OPEN state lasts
```

### Why These Numbers

- 5 failures: generous enough that transient hiccups don't trip it, strict enough
  that a genuinely down service gets blocked quickly
- 30s window: failures must be clustered to trip (5 failures over 10 minutes
  don't trip, 5 in 30s do)
- 30s cooldown: short enough that the user doesn't have to wait long, long enough
  to let rate limiting expire

### Integration with Model Selection

During Bootstrap, `SelfModelEngine.select_model()` checks the circuit breaker:

```
1. Default model CB is CLOSED? -> use it (fast path)
2. Default model CB is HALF_OPEN? -> use it (test attempt)
3. Default model CB is OPEN + cooldown expired? -> transition to HALF_OPEN
4. Default model CB is OPEN + cooldown active?
   -> Find best alternative with reliability > 0.5
   -> If found: auto-switch (set ctx.auto_switched = True)
   -> If not found: use default anyway with warning
```

## Model Selection Priority Algorithm

```python
def select_model(model, default_model_name, available_models):
    # Returns: (selected_name, auto_switched, reason)
```

The algorithm prioritizes keeping the user's chosen model unless there's
strong evidence it won't work:

```
Priority 1: Default model, CB is CLOSED
  -> (default, False, "")

Priority 2: Default model, CB is HALF_OPEN
  -> (default, False, "")     # one test attempt

Priority 3: Default model, CB is OPEN but cooldown expired
  -> transition to HALF_OPEN
  -> (default, False, "")

Priority 4: Default model is OPEN, find alternative
  -> scan available_models
  -> skip models with OPEN circuit breakers
  -> select highest reliability > 0.5
  -> (alternative, True, "reason string")

Priority 5: No better option
  -> (default, False, "warning string")
```

### Why No Cloud/Local Bias

Earlier versions preferred cloud models for planning and local for tasks.
This broke when cloud APIs were unreachable. The current system is
capability-based: it doesn't care where the model runs, only whether it works.

## Token Calibration Rolling Average

Each model's token estimates may be off because `len(text) // 4` is a rough
heuristic. When the API returns actual token counts (`_usage.prompt_tokens`),
we calibrate:

```python
def update_token_calibration(model, model_name, actual_tokens, estimated_tokens):
    ratio = actual / estimated
    rel.token_calibration = rel.token_calibration * 0.8 + ratio * 0.2
```

This is an exponential moving average (EMA) with alpha=0.2:
- Recent observations matter more than old ones
- Converges to the true ratio over ~10 observations
- Stored per-model because different tokenizers have different ratios

The calibration factor is currently stored but not yet used to adjust estimates.
Future work: multiply estimates by calibration before displaying.

## Capability Detection and Persistence

### What Capabilities Are Tracked

```python
capabilities: dict = {
    "tool_calling": True,   # Can this model handle function calling?
    "reasoning": True,      # Does this model emit thinking tokens?
}
```

### How Detection Works

**tool_calling**: starts as True (assumed). Set to False when an API returns
400 with "tool" or "function" in the error body. This is the self-healing
mechanism: one failure teaches permanently.

**reasoning**: starts as None (unknown). Set to True when thinking tokens are
received during streaming. Set to False after a full execution with no thinking
tokens when the `think: true` flag was sent.

### Persistence

Capabilities are stored in `model_reliability[name].capabilities` and written
to `self_model.json` on every persist. On next Bootstrap, the runtime reads
them to decide:
- Whether to include tools in the payload
- Whether to send the `think: true` flag
- Whether to expect thinking tokens (affects spinner behavior)

## scan_capabilities Implementation

Called during Bootstrap to count available system resources:

```python
def scan_capabilities(project_path=None) -> dict:
    # Scans: ~/.kognisant_core/ for global, .kognisant/ for project-local
    return {
        "scripts_count": N,            # .py files in scripts/
        "skills_count": N,             # .md files in skills/
        "custom_tools_count": N,       # .json files in tools/
        "active_jobs_count": N,        # jobs with status "active"
        "registered_projects_count": N, # entries in projects.json
        "specs_count": N,              # directories in specs/
        "goals_count": N,              # entries in goals.json
        "session_history_count": N,    # .json files in history/
    }
```

This data appears in the bootstrap line:
```
⚡ gemma4:latest | valence: +12 | 15 skills, 6 tools, 2 jobs active
```

### Why Scan Every Time

Capabilities change between executions (user adds a skill, a job completes).
The scan is cheap (a few `os.listdir` calls, ~1ms) and keeps the display
accurate without a file watcher.

## Cross-References

- [runtime-lifecycle.md](runtime-lifecycle.md) - Bootstrap loads SelfModel, Persist saves it
- [reflect-engine.md](reflect-engine.md) - HOT reflect updates valence and reliability
- [model-selection.md](model-selection.md) - Selection algorithm for agent swarm
- [telemetry-system.md](telemetry-system.md) - Records circuit breaker state per execution
