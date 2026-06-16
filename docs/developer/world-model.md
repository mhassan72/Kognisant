# World Model and Goal Generation

Technical documentation for the World Model subsystem, covering the observer layer, dependency graph, goal engine, and integration points.

## Overview

The World Model subsystem adds four new modules to Kognisant:

| Module | Responsibility |
| :--- | :--- |
| `models.py` | Shared dataclasses and utility functions |
| `observer.py` | Trace collection, static analysis, change detection, test tracking |
| `world_model.py` | In-memory graph, beliefs, contracts, gaps, maintenance engine |
| `world_model_store.py` | JSON-sharded persistence with atomic writes and snapshots |
| `goal_engine.py` | Goal generation, ranking, proposals, execution, and learning |

All features are gated behind `world_model_enabled: true` in `.kognisant/config.json`. Legacy users see no behavior change.

## Architecture

```
chat.py (/goals command, session-start display, inline suggestions)
    |
goal_engine.py (GoalGenerator, PriorityRanker, ProposalInterface,
                ExecutionEngine, LearningLoop, GraduatedAutonomyController)
    |  queries
world_model.py (DependencyGraph, BeliefSystem, ContractRegistry,
                EpistemicGapTracker, GraphMaintenanceEngine)
    |  persists via
world_model_store.py (WorldModelStore ABC, JsonWorldModelStore)
    |  feeds from
observer.py (TraceCollector, StaticAnalyzer, ChangeDetector, TestOutcomeTracker)
    |
agents.py (trace hooks)    daemon.py (scheduled jobs)    config.py (loaders)
```

## Data Models (models.py)

All dataclasses use `to_dict()` / `from_dict(cls, d)` for JSON round-tripping:

- **Node** - code entity (module, class, function) with file path, line range, module name, tags
- **Edge** - relationship (calls, imports, inherits, modifies) with confidence, provenance, version counter, stable/conditional flags
- **Belief** - tracked knowledge with provenance-based confidence, falsification count, archive status
- **Contract** - expected interface (args, return type, errors) between caller and callee nodes
- **EpistemicGap** - known unknown (unexercised function, untested branch, dynamic confirmation needed)
- **Goal** - improvement target with type, priority score, validation status, causal chain
- **TraceRecord / ToolCallTrace / FileOpTrace / LLMCallTrace** - PERP execution instrumentation
- **FeedbackSignal** - user response to a goal proposal (polarity, strength, source)

### Confidence System

Confidence scores are floats in [0.0, 1.0]. Initial values depend on provenance:

| Provenance | Initial Confidence |
| :--- | :--- |
| static (AST-derived) | 1.0 |
| user_assertion | 0.9 |
| dynamic (trace-observed) | 0.8 |
| llm_inference | 0.5 |

## Observer Layer (observer.py)

### TraceCollector

Thread-safe trace recording for PERP swarm executions.

- Uses `queue.Queue` for cross-thread submission (subtask threads push, main thread drains at session end)
- Incremental drain when queue exceeds 1000 entries
- Atomic write to `.kognisant/traces/<session_id>.json`
- All I/O wrapped in try/except (never interrupts PERP)

### StaticAnalyzer

AST-based Python source analysis.

- Extracts FunctionDef, AsyncFunctionDef (including nested), ClassDef, Import/ImportFrom, call sites
- Scope boundaries: .py files only, skips binary, respects .gitignore, resolves symlinks
- All edges get confidence=1.0, provenance="static"
- Computes cyclomatic complexity (If, While, For, With, ExceptHandler, BoolOp, Assert, comprehension)
- Handles syntax errors gracefully (log and skip)
- Creates external package leaf nodes for unresolvable imports

### ChangeDetector

Git-based change detection and World Model invalidation.

- Compares stored HEAD hash to current HEAD via `git diff --name-status`
- Identifies modified functions by overlapping diff hunks with AST line ranges
- Invalidation actions: reduce confidence 50% (modified), remove nodes (deleted), add nodes (new files), update paths (renames)
- First-run handling: creates change_log.json, skips confidence reduction
- Missing git: logs warning, skips detection

### TestOutcomeTracker

Pytest result tracking with instability and recovery detection.

- Rolling history of last 20 test runs in test_health.json
- Coverage mapping: test functions to source functions
- Instability: 3 consecutive failures tags nodes "unstable", reduces edge confidence by 40%
- Recovery: 3 consecutive passes removes "unstable" tag, reinforces edges by 10% of remaining distance to 1.0

## World Model Core (world_model.py)

### DependencyGraph

In-memory directed graph with dict-based adjacency storage.

- Nodes indexed by id, edges indexed by id plus source/target adjacency sets
- `query_reachable(node_id, max_hops, edge_types, min_confidence)`: BFS with LRU cache (100 entries, invalidated on edge version change)
- `merge_edge`: complementary evidence (max confidence when same type exists between same nodes)
- `mark_conditional`: reduces confidence by 20% for static-only edges without dynamic confirmation

### BeliefSystem

Knowledge store with provenance and decay.

- `add_belief`: overrides confidence based on provenance type
- `reinforce`: +10% of remaining distance to 1.0
- `contradict`: -30%, increment falsification count
- `apply_localized_decay`: 5% reduction for beliefs within 2 hops of modified nodes
- `prune_below_threshold(0.1)`: archives beliefs with removal reason

### ContractRegistry

Implicit interface contracts between components.

- Indexed by (source_node, target_node) tuple
- `check_violation`: reduces confidence by 20% on arg mismatch
- Violation events emitted once when confidence drops below 0.3 (reset on recovery above 0.3)
- `auto_register_from_signature`: creates contract at confidence=0.7 from static analysis
- `assert_contract`: user-declared at confidence=0.9

### EpistemicGapTracker

Explicit tracking of what the system does not know.

- Deduplication by (node_id, gap_type)
- Three gap types: unexercised_function (no dynamic edges after 5 executions), untested_branch (from coverage), dynamic_confirmation_needed (static-only after 10 ticks)
- Resolution when evidence arrives (dynamic edge or coverage data)
- Graceful skip when coverage data unavailable

### GraphMaintenanceEngine

Coordinates decay, reinforcement, cycle detection, and conflict resolution.

- `decay_tick`: BFS 2 hops from modified nodes, reduces edge confidence by 10%, firebreak at 3 hops
- Stable edge exemption: edges reinforced 30 consecutive ticks are exempt from decay
- Cycle detection: DFS-based, halts at cycle entry point
- Conflict resolution: retains higher confidence edge (last_reinforced as tiebreaker)
- Version counter: increments when confidence changes by >0.1 in single update

## Persistence (world_model_store.py)

### JsonWorldModelStore

Module-sharded JSON storage with atomic writes.

- Graph sharded by module: `graph/modules/<module>.json`, `graph/cross_module.json`, `graph/index.json`
- Atomic write: write to `.tmp` then `os.rename()`
- Shard size warning: logs once per session when shard exceeds 500KB (no splitting yet)
- Snapshot create: captures nodes + 2-hop neighbors to `snapshots/<timestamp>/`
- Snapshot restore: merges fragment back into current state with atomic writes
- Corrupted shard recovery: skips shard, marks affected edges with confidence 0.0

## Goal Engine (goal_engine.py)

### GoalGenerator

Six detection strategies:

1. **contract_violation** - polls ContractRegistry pending violations
2. **coverage_gap** - modules with >3 untested_branch gaps
3. **decay_alert** - >5 beliefs pruned from same module in one tick
4. **complexity** - cyclomatic complexity >15 AND (3+ modifications in 30 days OR no test coverage)
5. **stale_artifact** - file unmodified 90 days with nodes confidence <0.4
6. **pattern_detection** - same error 3 times in 5 executions (stub)

Deduplication: skips if active goal of same type targeting same node/file exists.

### Self-Validation

Cross-references static, dynamic, and test evidence:
- All 3 agree: "high_confidence" (no reduction)
- 2 agree: "partially_validated" (15% priority reduction)
- Disagree: "requires_user_review" (30% priority reduction)
- <2 sources available: "partially_validated" (15% reduction)

### PriorityRanker

Formula: `score = (impact_radius x severity_weight x likelihood) / effort_estimate`

| Goal Type | Severity Weight |
| :--- | :--- |
| contract_violation | 3.0 |
| pattern_detection | 2.8 |
| complexity | 2.5 |
| coverage_gap | 2.0 |
| decay_alert | 1.5 |
| stale_artifact | 1.0 |

Effort mapping: sum of files + functions in context mapped to 1-10 scale.

### LearningLoop

- Records accept (positive, 1.0), dismiss (negative, 1.0), ignore (negative, 0.5 after 3 sessions), manual_fix (positive, 0.5)
- Asymmetric weighting: negative signals carry 1.5x weight
- Acceptance rate computed over last 20 proposals per type per module
- Persists to `.kognisant/goals/learning.json`

### GraduatedAutonomyController

- auto_execute: rate > 85%
- suppress: rate < 20%
- ask: 20-85%
- Cold start: <20 total proposals, all goals require confirmation, confidence ceiling 0.7 for llm_inference
- Unsuppression: after 10 proposals across other types, one suppressed goal is re-evaluated
- Per-project rates with global fallback (weighted blend until local_count >= 20)

### ExecutionEngine

- Builds enriched PERP task description with causal chain (edges within 2 hops, confidence >0.3)
- Creates pre-execution snapshot (abort on failure)
- Executes via pluggable callback with 10-minute timeout (threading-based)
- On success: reinforces traversed edges, deletes snapshot
- On failure/timeout: restores snapshot, records failure reason
- Updates goal status in active.json / completed.json

## Integration Points

### agents.py

- **TraceCollector hooks**: session start/end, tool call recording, file op detection, LLM call timing
- **World Model PERSIST phase**: after existing context.md update, loads graph, reinforces edges touched during session, generates goals, saves state
- All operations guarded with `if world_model_enabled` + try/except

### daemon.py

- Job constants: `WM_JOB_DECAY_TICK`, `WM_JOB_STATIC_ANALYSIS`, `WM_JOB_GENERATE_GOALS`
- decay_tick: every 60 min when file modifications detected
- static_analysis: poll git HEAD every 5 min, run on change
- generate_goals: after successful decay_tick or static_analysis
- Failure handling: retry once after 5 min, mark failed on second failure

### chat.py

- `/goals` command with accept/dismiss subcommands
- Session-start goal display (top 3 by priority)
- Inline contextual suggestion on `/read` command
- `/goals` added to `/help` output

### config.py

- `is_world_model_enabled(project_root)`: checks config.json flag
- `load_world_model(project_root)`: returns JsonWorldModelStore instance
- `init_world_model(project_root)`: creates directory structure with empty JSON files
- `load_autonomy_config()` / `save_autonomy_config()`: global autonomy state
- `init_global_core()`: creates autonomy_config.json and goal_stats.json

## Testing

Test coverage for the World Model subsystem:

| Test File | Coverage |
| :--- | :--- |
| `test_world_model.py` | DependencyGraph, BeliefSystem, ContractRegistry, EpistemicGapTracker, GraphMaintenanceEngine |
| `test_world_model_store.py` | JsonWorldModelStore sharding, atomic writes, snapshots, corrupted file handling |
| `test_observer.py` | TraceCollector session lifecycle, thread safety |
| `test_static_analyzer.py` | AST extraction, complexity, scope boundaries, gitignore |
| `test_change_detector.py` | Git diff parsing, invalidation actions, first-run handling |
| `test_test_outcome_tracker.py` | Rolling history, instability, recovery |
| `test_goal_engine.py` | GoalGenerator strategies, self-validation, deduplication |
| `test_priority_ranker.py` | Formula, impact radius, effort mapping, tiebreakers |
| `test_learning_loop.py` | Signals, asymmetric weighting, acceptance rates |
| `test_graduated_autonomy.py` | Threshold transitions, cold start, unsuppression, global fallback |
| `test_proposal_interface.py` | Display, accept/dismiss, inline suggestions, critical notifications |
| `test_execution_engine.py` | Task description, snapshots, success/failure/timeout, status persistence |
| `test_config_world_model.py` | Config loaders, init_world_model, init_global_core |
| `test_daemon_wm_jobs.py` | Job constants, decay_tick/static_analysis/generate_goals execution, failure handling |
