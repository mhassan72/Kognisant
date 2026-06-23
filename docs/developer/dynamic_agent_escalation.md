# Dynamic Agent Escalation

## Problem Statement

The runtime's Plan phase classifies messages as SIMPLE/CONTEXT/COMPLEX, but COMPLEX
still runs as a single-model chat with a 3-round tool loop. Tasks that require
multi-step autonomous work (research + synthesis + file creation) exhaust the 3
rounds gathering information and never produce output.

Example: "Write an article comparing Kognisant to other systems" requires:
1. Browse GitHub repo (1-3 tool calls)
2. Research competitors (2-4 tool calls)
3. Synthesize findings (thinking)
4. Write article to file (1 tool call)

Total: 6-8 tool rounds minimum. The 3-round limit guarantees failure.

Currently, the user must manually type `/agent <task>` to invoke the PERP swarm.
The system should detect this need automatically in the Plan phase and escalate
without user intervention.


## Goals

- Plan phase detects when a task needs agent/swarm execution
- Automatic escalation to PERP swarm (no user action required)
- `/agent` remains available for explicit user-triggered agent mode
- User sees clear indicator that escalation happened
- Single-round tasks (most chat) stay fast with no overhead
- No false positives (simple questions should never trigger swarm)


## Design

### Execution Tier Classification

Extend the classifier from 3 tiers to 4:

| Tier | Criteria | Execution Mode |
|------|----------|---------------|
| SIMPLE | Short greeting, no tools needed | Single LLM call, 30s timeout |
| CONTEXT | Question about project, explanation | Single LLM call, 60s timeout |
| COMPLEX | Action verb + file/code, needs 1-3 tool calls | Single model, 3-round tool loop |
| AUTONOMOUS | Multi-step task needing 4+ tool rounds | PERP swarm (background agent) |

### AUTONOMOUS Detection (Plan Phase)

The planner determines AUTONOMOUS tier using two methods:

**Method 1: Rule-based pre-detection (fast, no LLM call)**

A message is AUTONOMOUS if ALL of:
- Already classified as COMPLEX (has action verbs, file refs, etc.)
- AND matches multi-step patterns:
  - Multiple distinct action verbs (research AND write, look at AND compare AND explain)
  - URL + creation intent (browse X then create/write Y)
  - Explicit multi-output request (write article, create report, build plan)
  - Word count > 50 with compound instructions

Patterns to detect:
```
"look at X and write Y"           -> 2 distinct outputs
"compare X to Y then write Z"    -> research + synthesis + output
"read A, analyze B, create C"    -> 3+ distinct steps
"research X and create a report" -> browse + file creation
```

**Method 2: Post-exhaustion escalation (fallback)**

If the runtime exhausts all 3 tool rounds without producing content output:
- Detect: tool_calls_made >= 3 AND response is empty/minimal
- Auto-escalate: rollback, invoke PERP swarm with the original user message
- Display: "Task requires more steps. Delegating to agent swarm..."

This catches cases the rule-based detector misses.


### Escalation Flow

```
User types: "write an article comparing Kognisant to other CLI AI systems"
  |
  v
fast_path_classifier.classify() -> "COMPLEX"
  |
  v
_detect_autonomous(message, classification) -> True
  Reason: multiple action verbs (look, compare, write) + creation intent (write article)
  |
  v
_plan prints: "📋 AUTONOMOUS -> delegating to agent swarm"
  |
  v
perp_orchestrate(user_message, project_info, compiled_models)
  |
  v
Swarm runs in background:
  - Planner breaks task into subtasks
  - Workers execute in parallel (research, write)
  - Result written to project file
  |
  v
User sees swarm progress (existing PERP output)
```


### _detect_autonomous Function

```python
def _detect_autonomous(message: str, classification: str) -> tuple[bool, str]:
    """Detect if a COMPLEX message should escalate to agent/swarm mode.
    
    Returns (should_escalate, reason).
    Only called when classification == "COMPLEX".
    """
    if classification != "COMPLEX":
        return (False, "")
    
    words = message.lower().split()
    
    # Count distinct action verb groups (research/browse vs write/create vs compare/analyze)
    research_verbs = {"look", "browse", "read", "fetch", "check", "explore", "research", "search", "find"}
    creation_verbs = {"write", "create", "generate", "build", "make", "produce", "draft"}
    analysis_verbs = {"compare", "analyze", "evaluate", "review", "assess", "explain"}
    
    has_research = bool(set(words) & research_verbs)
    has_creation = bool(set(words) & creation_verbs)
    has_analysis = bool(set(words) & analysis_verbs)
    
    distinct_phases = sum([has_research, has_creation, has_analysis])
    
    # Rule 1: 2+ distinct phases (research + write, or analyze + create)
    if distinct_phases >= 2:
        phases = []
        if has_research: phases.append("research")
        if has_analysis: phases.append("analysis")
        if has_creation: phases.append("creation")
        return (True, f"Multi-phase task detected: {' + '.join(phases)}")
    
    # Rule 2: URL + creation intent
    has_url = "http://" in message or "https://" in message
    if has_url and has_creation:
        return (True, "URL research + content creation")
    
    # Rule 3: Explicit multi-output markers
    multi_markers = ["then write", "then create", "and write", "and create",
                     "write an article", "write a report", "create a document",
                     "generate a report", "draft an article"]
    for marker in multi_markers:
        if marker in message.lower():
            return (True, f"Multi-output pattern: '{marker}'")
    
    # Rule 4: Very long compound instruction (50+ words with conjunctions)
    if len(words) > 50:
        conjunctions = sum(1 for w in words if w in ("and", "then", "also", "after"))
        if conjunctions >= 3:
            return (True, "Long compound instruction with multiple steps")
    
    return (False, "")
```


### Post-Exhaustion Escalation

In `_execute`, after the tool loop ends with no content:

```python
# After tool loop exhausts without producing content
if not ctx.response.strip() and ctx.tool_calls_made >= 3:
    # The model used all rounds researching but never wrote
    # Escalate to agent swarm
    _rollback(ctx)
    ctx.auto_escalated = True
    
    print("  Task needs more steps. Delegating to agent swarm...")
    
    from .agents import perp_orchestrate
    from .config import get_compiled_models
    compiled_models = get_compiled_models()
    perp_orchestrate(ctx.user_message, ctx.project_info, compiled_models)
    
    ctx.success = True
    ctx.response = "(Agent swarm dispatched. Use /status to monitor.)"
    ctx.streamed = False
    return
```


### User-Facing Changes

What the user sees when auto-escalation triggers:

```
You > write an article comparing Kognisant to other AI systems
⚡ Nemotron-3-ultra-550b-a55b | valence: +12 | 15 skills, 6 tools
📋 AUTONOMOUS -> delegating to agent swarm
  Multi-phase task detected: research + analysis + creation

  🐝 PERP Swarm Activated
  ────────────────────────────────────
  Planning with: Nemotron-3-ultra-550b-a55b
  Workers: 3 subtasks identified
    1. Research Kognisant architecture (browse GitHub)
    2. Research competitor CLI AI systems
    3. Write comparison article

  [Running in background - /status to monitor, /stop to cancel]
```


### Interaction with /agent Command

- `/agent <task>` - explicitly triggers PERP swarm (unchanged behavior)
- Normal chat message that triggers auto-escalation - same PERP swarm, auto-detected
- The only difference is the entry point:
  - `/agent`: user chose to escalate
  - Auto-escalation: planner chose to escalate
- Both use the same `perp_orchestrate()` function
- Both show the same swarm progress output


### Swarm Result Delivery

Auto-escalated swarms behave identically to `/agent`:
- Progress prints inline to the terminal while user stays at chat prompt
- User can continue chatting, use `/status` to check, or `/stop` to cancel
- On completion, the swarm summary prints with artifact references
- The user can always fall back to the created/updated files for full details


### Worker Display (Comprehensive)

Each worker in the swarm shows its full execution lifecycle in real-time:

```
🐝 Worker 1: Research Kognisant architecture
  ┌──────────────────────────────────────────────────────────────────────────┐
  │ Model: Nemotron-3-ultra-550b-a55b | Subtask: 1/3                        │
  │ Objective: Analyze Kognisant source code and document architecture       │
  ├──────────────────────────────────────────────────────────────────────────┤
  │ 💭 Reasoning:                                                            │
  │   1. Need to browse the main source directory for module structure       │
  │   2. Key modules: runtime.py (orchestrator), agents.py (swarm),          │
  │      self_model_engine.py (cognitive state)                              │
  │   3. Should also check the design docs for architectural decisions       │
  ├──────────────────────────────────────────────────────────────────────────┤
  │ 🔧 Actions:                                                              │
  │   ✓ Fetched github.com/mhassan72/Kognisant/tree/nightly (12.7s)         │
  │   ✓ Fetched .../cli_kognisant/ directory listing (10.2s)                 │
  │   ✓ Read docs/realignment.md (0.1s)                                     │
  │   ✓ Read cli_kognisant/runtime.py (0.1s)                                │
  ├──────────────────────────────────────────────────────────────────────────┤
  │ 📝 Output:                                                               │
  │   Kognisant uses a 5-phase cognitive lifecycle (Bootstrap, Plan,         │
  │   Execute, Reflect, Persist). Key differentiators: self-model engine     │
  │   with Bayesian reliability tracking, circuit breakers per model,        │
  │   persistent valence state, PERP swarm for autonomous tasks...           │
  │   (847 tokens)                                                           │
  ├──────────────────────────────────────────────────────────────────────────┤
  │ ⏱️  Total: 58.3s | 4 tool calls | Tokens: 5,200 in / 847 out            │
  └──────────────────────────────────────────────────────────────────────────┘
```

Worker display sections:
- **Header**: model used, subtask number/total, objective description
- **Reasoning**: full thinking steps (numbered), streamed live in gray as they arrive
- **Actions**: every tool call with target, status icon, and duration
  - Live: spinner while active (`◐ Fetching...`)
  - Done: checkmark with result (`✓ Fetched ... (12.7s)`)
  - Failed: cross with error (`✗ Failed to fetch ... (timeout)`)
- **Output**: the worker's produced content (truncated to 3-4 lines with token count)
- **Footer**: total time, tool call count, token usage (in/out)

The actions section updates in real-time. Reasoning prints first once thinking
completes, then actions appear one by one as tools execute.


### Token Tracking (Per-Agent and Total)

Every agent in the swarm pipeline tracks its own token usage independently:

**Per-agent tracking:**
- Planner: tokens used to decompose the task into subtasks
- Each Worker: tokens used for its subtask (reasoning + tool loop + output)
- Reflector: tokens used to verify and consolidate results

**Swarm completion summary with token breakdown:**

```
🐝 Swarm Complete (3/3 subtasks)
  ┌──────────────────────────────────────────────────────────────────────────┐
  │ Token Usage                                                              │
  ├──────────────────────────────────────────────────────────────────────────┤
  │   Planner:   1,200 in /   450 out                                       │
  │   Worker 1:  5,200 in /   847 out  (Research Kognisant)                  │
  │   Worker 2:  3,800 in /   620 out  (Research competitors)                │
  │   Worker 3:  8,100 in / 2,400 out  (Write article)                      │
  │   Reflector:   900 in /   320 out                                       │
  ├──────────────────────────────────────────────────────────────────────────┤
  │   Total:    19,200 in / 4,637 out                                        │
  │   Time:     168.5s | Models used: Nemotron-550B                          │
  ├──────────────────────────────────────────────────────────────────────────┤
  │ 📄 Artifacts (3):                                                        │
  │   ✓ created  docs/kognisant_comparison_article.md                        │
  │   ✓ created  docs/competitor_analysis.md                                 │
  │   ~ modified cli_kognisant/README.md                                     │
  └──────────────────────────────────────────────────────────────────────────┘
```

**Artifact tracking:**
- Each file operation (create, modify, delete) across all workers is collected
- Deduplicated: if Worker 1 creates a file and Worker 3 modifies it, show final state
- Each artifact shows its action icon:
  - `✓ created` - new file
  - `~ modified` - existing file updated
  - `✗ deleted` - file removed

**Telemetry integration:**
- Per-swarm token totals are appended to telemetry.jsonl
- `/telemetry` shows swarm executions with total tokens
- Per-worker breakdown available in the trace file (existing trace system)

**Thinking storage for agents:**
- Each worker's reasoning is saved to the session thinking file
- Entries include `"source": "planner"`, `"source": "worker_1"`, etc.
- `/thinking list` shows both chat and agent thinking entries


### Safeguards Against False Positives

The AUTONOMOUS classifier should NOT trigger for:
- "fix the bug in auth.py" - single action, 1-2 tool calls sufficient
- "read main.py and explain it" - read + respond, no multi-output
- "what does this function do?" - question, no creation
- "refactor the auth module" - single action, even if complex

It SHOULD trigger for:
- "look at the repo and write a README" - research + creation
- "compare our API to Stripe's and write a report" - research + analysis + creation
- "browse the docs, find examples, and create test files" - 3 distinct phases
- "research best practices for auth and implement them" - research + multi-file creation


### Configuration

Add to SelfModel capabilities:
```python
capabilities: {
    "tool_calling": true,
    "reasoning": true,
    "auto_escalation": true   # can be disabled by user if they prefer manual /agent
}
```

User can disable auto-escalation:
- Set in model config: `"auto_escalation": false`
- Or globally in self_model.json


## Implementation Order

1. Add `_detect_autonomous()` function to runtime.py (rule-based detection)
2. Modify `_plan()` to call detection and set `ctx.autonomous = True`
3. Add auto-escalation path in `_plan()` that invokes `perp_orchestrate()`
4. Add post-exhaustion fallback in `_execute()` (3 rounds, no content -> escalate)
5. Add AUTONOMOUS display line (📋 AUTONOMOUS -> delegating...)
6. Modify `run_subtask_agent` in agents.py to stream thinking tokens per worker
7. Add comprehensive worker display (header, reasoning, actions, output, footer)
8. Add per-agent token tracking in `_orchestrate_worker`
9. Add swarm completion summary with token breakdown and artifact list
10. Track all file operations across workers for artifact summary
11. Save agent thinking to session thinking file with source field
12. Add `auto_escalation` capability to SelfModel for user opt-out
13. Integrate swarm token totals into telemetry
14. Tests: verify detection patterns, verify escalation triggers, verify worker display,
    verify token accounting, verify artifact collection


## Success Criteria

- "Write an article comparing X to Y" auto-escalates to swarm
- "fix the bug in auth.py" stays in normal COMPLEX mode
- Post-exhaustion fallback catches cases the rule-based detector misses
- User sees clear indicator when escalation happens
- `/agent` still works for explicit escalation
- Each worker shows full lifecycle (reasoning, actions, output, tokens)
- Swarm summary shows per-agent token breakdown and total
- Multi-artifact output lists all created/modified/deleted files
- Agent thinking saved to session thinking file
- No regressions on SIMPLE/CONTEXT/COMPLEX paths
