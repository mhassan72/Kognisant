# Dynamic Model Selection for Agent Swarm

## Problem Statement

`get_best_models_pool()` has a hardcoded bias: it always picks cloud models for
planning and only uses local models as task worker fallbacks. This causes failures
when:
- Cloud API credits are exhausted (HTTP 402)
- Cloud API keys are expired (HTTP 401)
- Cloud endpoints are rate limited (HTTP 429)
- The only working model is local (Ollama)

The user's active chat model (gemma4 local) works fine, but the swarm planner
ignores it and picks a dead cloud model, then gives up entirely instead of
falling back.


## Goals

- Remove hardcoded cloud/local bias from model selection
- Select models based on proven capabilities (reasoning: true/false)
- Cascade through the pool on failure instead of giving up
- Record capability discoveries in the model pool for future use
- Use the same capability detection already built in the runtime


## Design

### Model Pool Schema (capabilities field)

Each model in `models_pool.json` gets a `capabilities` object:

```json
{
  "vendor": "Ollama (Local)",
  "name": "gemma4:latest",
  "model_id": "gemma4:latest",
  "protocol": "ollama",
  "api_base_url": "http://localhost:11434",
  "capabilities": {
    "tool_calling": true,
    "reasoning": true
  }
}
```

Field values:
- `true` - proven capability (model demonstrated it)
- `false` - proven incapability (model was tested and failed)
- absent/null - unknown (not yet tested)

These are persisted in `models_pool.json` and in `self_model.json`
(model_reliability[name].capabilities). The pool file is the user-facing config,
self_model is the runtime's learned state.


### New Selection Logic (replaces get_best_models_pool)

```python
def get_best_models_pool(compiled_models):
    """Select planner and task models based on capabilities, not provider type.
    
    Priority for planner (needs reasoning):
      1. Models with capabilities.reasoning == true + reachable
      2. Models with capabilities.reasoning == null (unknown, try them)
      3. Any model that's reachable (last resort)
    
    Priority for task workers (needs tool_calling):
      1. Models with capabilities.tool_calling == true + reachable
      2. Any reachable model
    
    On failure (402, 401, 429, timeout):
      - Mark model as unreachable for this session
      - Try the next model in priority order
      - Never give up until all models are exhausted
    """
```

### Selection Priority Order

For the planner role:
1. Reasoning-capable models sorted by reliability (from SelfModel)
2. Unknown-capability models (worth trying)
3. Skip models with `reasoning: false`
4. Skip models with known connection failures in this session

For task workers:
1. Tool-calling capable models sorted by reliability
2. Unknown models
3. Skip models with `tool_calling: false`

### Cascading Fallback on Failure

When the selected planner model fails:

```
Try model A (reasoning: true, highest reliability)
  |
  v (HTTP 402 - no credits)
Mark A as "session_unreachable", try next
  |
  v
Try model B (reasoning: true, next highest reliability)
  |
  v (HTTP 401 - bad key)
Mark B as "session_unreachable", try next
  |
  v
Try model C (reasoning: null, unknown - local Ollama)
  |
  v (success! also discovered reasoning: true)
Update C's capabilities: reasoning = true
Use C as planner going forward
```

The cascade happens inside `_orchestrate_worker`, wrapping the planner LLM call:

```python
def _plan_with_fallback(task, compiled_models, project_info):
    """Try each reasoning-capable model until one works."""
    candidates = _get_planner_candidates(compiled_models)
    
    for model in candidates:
        try:
            plan = _call_planner(model, task, project_info)
            if plan:
                # Success - record this model works
                return plan, model
        except Exception as e:
            error_str = str(e)
            if "402" in error_str or "401" in error_str or "429" in error_str:
                # Payment/auth/rate - skip this model for the session
                _mark_session_unreachable(model)
                continue
            elif "timeout" in error_str.lower():
                _mark_session_unreachable(model)
                continue
            else:
                raise  # Unknown error, don't swallow
    
    # All models exhausted
    raise Exception("All models in pool are unreachable or incapable")
```


### Capability Discovery Integration

The runtime already detects `reasoning` capability during chat (from the
thinking/uptime plan). The swarm should use the same data:

1. Runtime detects reasoning during chat -> saves to `self_model.model_reliability[name].capabilities`
2. Swarm reads `self_model.model_reliability` at startup to know which models reason
3. If a model's capability is still unknown, the swarm tries it and records the result
4. The `models_pool.json` capabilities field serves as user-override (manually set)

Priority: `models_pool.json` capabilities > `self_model` learned capabilities > unknown


### Session-Level Unreachable Tracking

Models that fail with 401/402/429 during a session are marked unreachable
for that session only (not permanently). This prevents retrying dead APIs
every turn but allows recovery on next session (maybe credits were added).

```python
# In-memory only, not persisted
_session_unreachable: set[str] = set()

def _mark_session_unreachable(model_name: str):
    _session_unreachable.add(model_name)

def _is_reachable(model_name: str) -> bool:
    return model_name not in _session_unreachable
```


### Active Model Priority

When auto-escalating from chat to swarm, the currently active model (the one
that just successfully handled "hello") should be prioritized:

```python
def get_best_models_pool(compiled_models, active_model_name=None):
    # If active_model has reasoning capability, use it as planner
    # This respects the user's explicit model choice
```

The runtime passes `ctx.active_model["name"]` to `perp_orchestrate()` so the
swarm knows which model is currently working.


## Implementation Order

1. Add `capabilities` field handling to model pool loading in config.py
2. Rewrite `get_best_models_pool()` to use capability-based selection
3. Add `_plan_with_fallback()` cascade logic in agents.py
4. Add session-level unreachable tracking (in-memory set)
5. Pass active model name from runtime to perp_orchestrate
6. Read self_model capabilities as secondary source
7. Update capability after successful planner use
8. Tests: verify selection priority, verify cascade on 402, verify local fallback


## Success Criteria

- Swarm planner uses gemma4 (local) when cloud APIs are out of credits
- Models with proven reasoning are preferred for planning
- HTTP 402/401/429 triggers cascade to next model (no immediate failure)
- Capabilities are recorded and reused across sessions
- Active chat model gets priority when escalating to swarm
- No regressions for users with working cloud APIs
