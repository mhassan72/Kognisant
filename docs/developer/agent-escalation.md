# Agent Escalation (Dynamic Auto-Escalation)

How Kognisant detects when a task exceeds single-model chat capacity and
automatically delegates to the PERP agent swarm.

## Why Auto-Escalation

The runtime's Execute phase runs a 3-round tool loop. Each round:
1. Model receives context + tool results so far
2. Model calls 1+ tools OR produces content
3. Tool results are appended to context

Many real tasks need more than 3 rounds:

```
Task: "Write an article comparing Kognisant to other systems"
  Round 1: browse GitHub repo (1-2 tool calls)
  Round 2: research competitors (2-3 tool calls)
  Round 3: analyze findings (thinking)
  --- 3 rounds exhausted, no content produced ---
  The model never got to write the article.
```

Previously, users had to know to type `/agent <task>` for these cases. The
system should detect multi-step intent automatically.

## The 3-Round Limit Problem

```
┌────────────────────────────────────────────────────────────┐
│ Single-Model Chat (COMPLEX tier)                           │
│                                                            │
│ Round 1: model calls tools -> results appended             │
│ Round 2: model calls tools -> results appended             │
│ Round 3: model calls tools -> results appended             │
│                                                            │
│ If no content after 3 rounds: execution "succeeds" with    │
│ empty response. User gets nothing.                         │
└────────────────────────────────────────────────────────────┘

vs.

┌────────────────────────────────────────────────────────────┐
│ PERP Agent Swarm (AUTONOMOUS tier)                         │
│                                                            │
│ Planner: decompose task into subtasks                      │
│ Worker 1: research (unlimited tool rounds per worker)      │
│ Worker 2: research (parallel with Worker 1)                │
│ Worker 3: synthesize + write (uses Worker 1+2 output)      │
│ Reflector: verify completeness                             │
│                                                            │
│ Each worker gets its own context, own tool loop, own       │
│ token budget. Total capacity: unlimited.                   │
└────────────────────────────────────────────────────────────┘
```

## _detect_autonomous Rules

Called from the Plan phase when classification is COMPLEX:

```python
def _detect_autonomous(message: str) -> tuple[bool, str]:
```

### Verb Groups

```python
_RESEARCH_VERBS = {
    "look", "browse", "fetch", "check", "explore",
    "research", "search", "find", "inspect"
}
_CREATION_VERBS = {
    "write", "create", "generate", "build", "make",
    "produce", "draft", "compose", "author"
}
_ANALYSIS_VERBS = {
    "compare", "analyze", "evaluate", "assess",
    "contrast", "benchmark"
}
```

### Rule 1: Multi-Phase Detection

```python
has_research = bool(set(words) & _RESEARCH_VERBS)
has_creation = bool(set(words) & _CREATION_VERBS)
has_analysis = bool(set(words) & _ANALYSIS_VERBS)

distinct_phases = sum([has_research, has_creation, has_analysis])

if distinct_phases >= 2:
    return (True, f"Multi-phase task detected: {' + '.join(phases)}")
```

Triggers when the message contains verbs from 2+ different groups.
"research and write" = research + creation = 2 phases. "fix the bug" = only
creation = 1 phase (not triggered).

### Rule 2: URL + Creation

```python
has_url = "http://" in message or "https://" in message
if has_url and has_creation:
    return (True, "URL research + content creation")
```

A URL combined with creation intent means "go read this, then produce something
from it." That's inherently multi-step (browse + synthesize + write).

### Rule 3: Multi-Output Markers

```python
_MULTI_OUTPUT_MARKERS = [
    "then write", "then create", "and write", "and create",
    "write an article", "write a report", "create a document",
    "generate a report", "draft an article", "write a comparison",
    "write a summary", "create a plan", "build a report",
    "write documentation", "create documentation",
]
```

These are phrases that explicitly indicate a multi-step workflow with a
substantial output artifact. "write an article" implies research first.

### Rule 4: Long Compound Instructions

```python
if len(words) > 50:
    conjunctions = sum(1 for w in words if w in ("and", "then", "also", "after", "next"))
    if conjunctions >= 3:
        return (True, "Long compound instruction with multiple steps")
```

A 50+ word message with 3+ coordination conjunctions is essentially a
multi-paragraph task specification that needs decomposition.

## Multi-Output Markers List

The full set of phrases that trigger Rule 3:

| Marker | Why It Indicates Multi-Step |
|--------|---------------------------|
| "then write" | Sequential: do X, THEN produce output |
| "then create" | Sequential: do X, THEN produce artifact |
| "and write" | Compound: do X AND produce output |
| "and create" | Compound: do X AND produce artifact |
| "write an article" | Articles require research first |
| "write a report" | Reports require data gathering |
| "create a document" | Documents need content from somewhere |
| "generate a report" | Same as write a report |
| "draft an article" | Same as write an article |
| "write a comparison" | Comparisons need data from 2+ sources |
| "write a summary" | Summaries need source material |
| "create a plan" | Plans need analysis of current state |
| "build a report" | Same as write a report |
| "write documentation" | Docs need code reading/understanding |
| "create documentation" | Same as write documentation |

## Post-Exhaustion Fallback

The second detection path: if the rule-based detector misses a multi-step
task, the Execute phase catches it after exhaustion.

```python
# After _execute completes:
if not ctx.success and not ctx.cancelled and ctx.tool_calls_made >= 3 and not ctx.response.strip():
    # Model used all rounds researching but never produced output
    print("Task needs more steps. Auto-escalating to agent swarm...")
    _rollback(ctx)
    _escalate_to_swarm(ctx)
```

### Detection Criteria

ALL must be true:
- Execution not marked successful
- User didn't cancel (not Ctrl+C)
- 3+ tool calls were made (model was trying to work)
- Response is empty or whitespace-only (never produced content)

### Why All Four Conditions

- `not success`: don't escalate if the model actually answered
- `not cancelled`: don't escalate if the user aborted intentionally
- `tool_calls_made >= 3`: distinguishes from simple errors (0 tool calls = API
  failure, not tool exhaustion)
- `not response.strip()`: the model might have produced partial content that's
  useful, don't discard that

## Integration with perp_orchestrate

```python
def _escalate_to_swarm(ctx: ExecutionContext) -> None:
    from .agents import perp_orchestrate
    from .config import get_compiled_models

    compiled_models = get_compiled_models()
    ctx.project_info["_active_model_name"] = ctx.active_model.get("name", "")

    perp_orchestrate(ctx.user_message, ctx.project_info, compiled_models)

    ctx.success = True
    ctx.response = "(Agent swarm dispatched. Use /status to monitor progress.)"
    ctx.streamed = False
```

The swarm receives:
- Original user message (unmodified)
- Project info (with `_active_model_name` injected for model priority)
- Full compiled model pool (for cascading selection)

The swarm runs in the background. The runtime marks the execution as
successful and returns control to the chat prompt.

## Safeguards Against False Positives

### Messages That Should NOT Trigger

| Message | Why NOT | Classification |
|---------|---------|----------------|
| "fix the bug in auth.py" | 1 phase (creation only) | COMPLEX |
| "read main.py and explain it" | "explain" is not in creation verbs | COMPLEX |
| "what does this function do?" | No action verbs | CONTEXT |
| "refactor the auth module" | 1 phase (creation only) | COMPLEX |
| "write a test for login" | 1 phase (creation), no research | COMPLEX |
| "look at main.py" | 1 phase (research), no creation | COMPLEX |

### Why "read and explain" Doesn't Trigger

"read" is in `_RESEARCH_VERBS`, but "explain" is NOT in `_CREATION_VERBS`.
Explaining is conversational output, not artifact creation. The system
distinguishes between "produce text as conversation" and "produce text as file."

### Why "write a test" Doesn't Trigger

"write" is in `_CREATION_VERBS`, but there's no research or analysis verb.
Writing a test is a single-step action: read the source, write the test.
2-3 tool rounds handle this fine.

## Worker Display Design

Each swarm worker shows its lifecycle in a comprehensive box:

```
🐝 Worker 1: Research Kognisant architecture
  ┌──────────────────────────────────────────────────────────────────────────┐
  │ Model: gemma4:latest | Subtask: 1/3                                      │
  │ Objective: Analyze Kognisant source code and document architecture       │
  ├──────────────────────────────────────────────────────────────────────────┤
  │ 💭 Reasoning:                                                            │
  │   1. Need to browse the main source directory for module structure       │
  │   2. Key modules: runtime.py, agents.py, self_model_engine.py            │
  │   3. Should also check design docs for architectural decisions           │
  ├──────────────────────────────────────────────────────────────────────────┤
  │ 🔧 Actions:                                                              │
  │   ✓ Fetched github.com/.../cli_kognisant/ (12.7s)                        │
  │   ✓ Read docs/realignment.md (0.1s)                                      │
  │   ✓ Read cli_kognisant/runtime.py (0.1s)                                 │
  │   ✗ Failed to fetch external.com/api (timeout)                           │
  ├──────────────────────────────────────────────────────────────────────────┤
  │ 📝 Output: 847 tokens                                                    │
  │   Kognisant uses a 5-phase cognitive lifecycle (Bootstrap, Plan,         │
  │   Execute, Reflect, Persist)...                                          │
  ├──────────────────────────────────────────────────────────────────────────┤
  │ ⏱️  Total: 58.3s | 4 tool calls | Tokens: 5,200 in / 847 out            │
  └──────────────────────────────────────────────────────────────────────────┘
```

### Sections

| Section | Content |
|---------|---------|
| Header | Model name, subtask number/total, objective |
| Reasoning | Parsed thinking steps (numbered), shown in dim |
| Actions | Per-tool-call: icon + target + duration |
| Output | Worker's produced content (truncated, with token count) |
| Footer | Total time, tool count, token in/out |

### Action Icons

```
✓  - tool succeeded
✗  - tool failed
◐  - tool in progress (live animation, replaced when done)
```

## Per-Agent Token Tracking

Each agent in the PERP pipeline tracks its own token usage:

```
┌─────────────────────────────────────────────────────┐
│ Agent          │ Tokens In │ Tokens Out │ Duration  │
├─────────────────────────────────────────────────────┤
│ Planner        │   1,200   │     450    │    8.2s   │
│ Worker 1       │   5,200   │     847    │   58.3s   │
│ Worker 2       │   3,800   │     620    │   42.1s   │
│ Worker 3       │   8,100   │   2,400    │   95.7s   │
│ Reflector      │     900   │     320    │    5.2s   │
├─────────────────────────────────────────────────────┤
│ Total          │  19,200   │   4,637    │  168.5s   │
└─────────────────────────────────────────────────────┘
```

Token tracking is per-worker using the same `estimate_tokens()` heuristic
as the runtime. When the API returns `_usage`, actual counts are used instead.

## Artifact Collection and Deduplication

All file operations across all workers are collected into a final artifact list:

```python
# Each tool call that creates/modifies/deletes a file is recorded
artifacts = {}  # path -> {"action": "created"|"modified"|"deleted", "worker": N}

# Deduplication: last write wins
# Worker 1 creates docs/comparison.md
# Worker 3 modifies docs/comparison.md
# Final artifact: docs/comparison.md -> "modified" (by Worker 3)
```

### Swarm Completion Summary

```
🐝 Swarm Complete (3/3 subtasks)
  ┌──────────────────────────────────────────────────────────────────────────┐
  │ 📄 Artifacts (3):                                                        │
  │   ✓ created  docs/kognisant_comparison_article.md                        │
  │   ✓ created  docs/competitor_analysis.md                                 │
  │   ~ modified cli_kognisant/README.md                                     │
  ├──────────────────────────────────────────────────────────────────────────┤
  │ Tokens: 19,200 in / 4,637 out | Time: 168.5s                            │
  └──────────────────────────────────────────────────────────────────────────┘
```

### Artifact Action Icons

```
✓ created   - new file written
~ modified  - existing file updated
✗ deleted   - file removed
```

## Telemetry Integration

Auto-escalated swarm executions appear in telemetry with:
- `classification: "AUTONOMOUS"` in the triggering execution record
- Per-swarm token totals appended as a separate telemetry record
- `/telemetry` shows swarm executions with their total token cost

Agent thinking is saved to the session thinking file with a `source` field:
```json
{"turn": 3, "source": "planner", "reasoning": [...]}
{"turn": 3, "source": "worker_1", "reasoning": [...]}
{"turn": 3, "source": "worker_2", "reasoning": [...]}
```

## Cross-References

- [runtime-lifecycle.md](runtime-lifecycle.md) - Execute phase triggers escalation
- [fast-path-classifier.md](fast-path-classifier.md) - COMPLEX classification pre-req
- [model-selection.md](model-selection.md) - Cascading selection for swarm planner
- [thinking-and-reasoning.md](thinking-and-reasoning.md) - Worker thinking display
