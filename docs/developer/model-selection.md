# Model Selection (Dynamic Capability-Based)

How Kognisant selects models for both single-message chat and agent swarm
planning, using learned capabilities and cascading fallback on failure.

## Why We Removed the Cloud/Local Bias

The original `get_best_models_pool()` had hardcoded rules:
- Cloud models for planning (assumed better reasoning)
- Local models only as task worker fallbacks

This broke when:
- Cloud API credits exhausted (HTTP 402) - planner gives up entirely
- Cloud API keys expired (HTTP 401) - same failure
- Rate limited (HTTP 429) - same
- Only a local model works - system ignores the working model

The fix: select models based on what they've PROVEN they can do, not where
they run. A local gemma4 with `reasoning: true` is a better planner than
a cloud GPT-4 that returns 402.

## Capability-Based Priority

### For Planner Role (needs reasoning)

```
Priority 1: Active model (if reasoning-capable)
            User explicitly chose this model, it's working, respect that choice

Priority 2: Models with capabilities.reasoning == true
            Sorted by reliability score (higher is better)
            These have PROVEN they can reason

Priority 3: Models with capabilities.reasoning == null (unknown)
            Worth trying - might discover reasoning capability

Priority 4: Any reachable model
            Last resort - better than failing entirely

Skip: Models with reasoning == false (proven can't reason)
Skip: Models in _session_unreachable set
```

### For Task Worker Role (needs tool calling)

```
Priority 1: Models with capabilities.tool_calling == true
Priority 2: Any reachable model
Skip: Models with tool_calling == false
```

### Capability Sources (Priority Order)

```
1. models_pool.json "capabilities" field (user override - highest authority)
2. self_model.model_reliability[name].capabilities (runtime learned)
3. null/absent (unknown - try and learn)
```

The pool file is the user's manual configuration. If they say a model has
reasoning, trust that over the runtime's learned state. This allows users
to fix false negatives (runtime failed to detect reasoning due to a one-time
network issue).

## Cascading Fallback on Failure

When a selected model fails during agent swarm planning:

```
_plan_with_fallback(task, compiled_models, project_info):
│
├─ candidates = _get_planner_candidates(compiled_models)
│   (ordered by priority: active > reasoning:true > unknown > any)
│
├─ FOR model IN candidates:
│   │
│   ├─ TRY: result = _call_planner(model, task, project_info)
│   │   ├─ Success? -> return (result, model)
│   │   └─ Error? -> check error type below
│   │
│   ├─ HTTP 401 (auth failed):
│   │   └─ _mark_session_unreachable(model), continue
│   │
│   ├─ HTTP 402 (payment required):
│   │   └─ _mark_session_unreachable(model), continue
│   │
│   ├─ HTTP 403 (forbidden):
│   │   └─ _mark_session_unreachable(model), continue
│   │
│   ├─ HTTP 429 (rate limited):
│   │   └─ _mark_session_unreachable(model), continue
│   │
│   ├─ Timeout:
│   │   └─ _mark_session_unreachable(model), continue
│   │
│   └─ Unknown error:
│       └─ raise (don't swallow unexpected failures)
│
└─ All exhausted: raise "All models in pool are unreachable or incapable"
```

### Why Cascade Instead of Retry

Retrying the same model on 402/401 is pointless - the credits won't refill
in 2 seconds. The correct action is to try the next candidate. This is
fundamentally different from the runtime's retry (which handles transient
timeouts on the SAME model).

## Session-Level Unreachable Tracking

```python
# In-memory only, reset on new session
_session_unreachable: set[str] = set()

def _mark_session_unreachable(model_name: str):
    _session_unreachable.add(model_name)

def _is_reachable(model_name: str) -> bool:
    return model_name not in _session_unreachable
```

### Why In-Memory Only

- Credits may be refilled between sessions
- API keys may be rotated
- Rate limits expire naturally
- Next session should try again with fresh state

### Why Not Per-Model Persistent

Permanent unreachable marking would require a "try again" mechanism (user
command to clear the flag). In-memory per-session avoids this UX complexity
while still preventing wasted retries within a single session.

## Active Model Priority

When the runtime auto-escalates from chat to agent swarm, the currently
active chat model gets top priority for planning:

```python
def get_best_models_pool(compiled_models, active_model_name=None):
    # If active_model has reasoning capability, use it as planner first
    # Rationale: it JUST worked for chat, proven reachable right now
```

The runtime passes the active model to the swarm:

```python
# In _escalate_to_swarm():
ctx.project_info["_active_model_name"] = ctx.active_model.get("name", "")
perp_orchestrate(ctx.user_message, ctx.project_info, compiled_models)
```

### Why This Matters

Consider: user is chatting with local gemma4 (working fine). They type a
complex task that triggers AUTONOMOUS escalation. Without active model priority,
the swarm might try cloud GPT-4 first (higher reliability from past sessions),
get a 402, then cascade to gemma4. With active model priority, it uses gemma4
immediately (it's proven working THIS SECOND).

## _get_planner_candidates Implementation

```python
def _get_planner_candidates(compiled_models, active_model_name=None):
    """Return models ordered by planner suitability.

    Order:
      1. Active model (if reasoning-capable and reachable)
      2. Reasoning-proven models, sorted by reliability descending
      3. Unknown-capability models, sorted by reliability descending
      4. Any remaining reachable models (desperate fallback)
    """
    # Load self_model for learned capabilities
    self_model = SelfModelEngine.load()

    tier_1 = []  # Active model
    tier_2 = []  # reasoning: true
    tier_3 = []  # reasoning: unknown
    tier_4 = []  # anything else

    for model in compiled_models:
        name = model.get("name", "")
        if not _is_reachable(name):
            continue

        reasoning = _get_reasoning_capability(model)  # True/False/None

        if name == active_model_name and reasoning is not False:
            tier_1.append(model)
        elif reasoning is True:
            tier_2.append((model, _get_reliability(model)))
        elif reasoning is None:
            tier_3.append((model, _get_reliability(model)))
        elif reasoning is not False:
            tier_4.append(model)

    # Sort tier 2 and 3 by reliability (higher first)
    tier_2.sort(key=lambda x: x[1], reverse=True)
    tier_3.sort(key=lambda x: x[1], reverse=True)

    result = tier_1
    result += [m for m, _ in tier_2]
    result += [m for m, _ in tier_3]
    result += tier_4
    return result
```

### Reliability Scoring

```python
def _get_reliability(model):
    name = model.get("name", "")
    rel = self_model.model_reliability.get(name)
    if rel:
        return rel.reliability  # Bayesian: (s+1)/(s+f+2)
    return 0.5  # Unknown model starts at neutral
```

## Integration with SelfModel Reliability Data

The selection algorithm reads from two places:

```
┌──────────────────────┐       ┌─────────────────────────┐
│  models_pool.json    │       │  self_model.json         │
│  (user config)       │       │  (runtime learned)       │
├──────────────────────┤       ├─────────────────────────┤
│  capabilities:       │       │  model_reliability:      │
│    reasoning: true   │ pri 1 │    reliability: 0.85     │
│    tool_calling: true│       │    capabilities:         │
│                      │       │      reasoning: true     │ pri 2
└──────────────────────┘       │      tool_calling: true  │
                               │    avg_response_time: 3s │
                               └─────────────────────────┘
```

Pool config capabilities override learned capabilities. Learned capabilities
override unknown. This three-level priority ensures:
- Users can fix detection errors (pool config)
- Runtime learns from experience (self_model)
- New models get a fair chance (unknown = try it)

## Cross-References

- [self-model-engine.md](self-model-engine.md) - Bayesian reliability and circuit breakers
- [agent-escalation.md](agent-escalation.md) - Swarm uses this selection for planning
- [runtime-lifecycle.md](runtime-lifecycle.md) - Bootstrap phase model selection
- [thinking-and-reasoning.md](thinking-and-reasoning.md) - Reasoning capability detection
