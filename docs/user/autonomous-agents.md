# Autonomous Agents

The agent system is Kognisant's most powerful feature. When you have a multi-step task that would require constant back-and-forth with a regular AI, the PERP swarm handles it end-to-end: planning, executing in parallel, validating results, and persisting learnings.

---

## Why Use Agents?

Normal chat is great for quick questions and small edits. But when the task involves multiple files, research, creation, and validation, you need more than one pass. The agent system:

- Breaks complex work into manageable subtasks
- Executes subtasks in parallel (not sequentially)
- Validates its own output before committing
- Updates your project memory with what it learned
- Tracks token usage and artifacts per worker

You describe what you want in plain English. The swarm figures out how to get there.

---

## The /agent Command

Inside a chat session, prefix your task with `/agent`:

```
/agent Write comprehensive tests for the authentication module
```

```
/agent Research rate limiting best practices and implement a sliding window limiter
```

```
/agent Refactor the database layer to use connection pooling
```

The task description can be as detailed or as high-level as you like. More detail means better planning; less detail means the AI makes more decisions autonomously.

---

## The 4 PERP Stages

Every agent workflow follows four strict stages:

### 1. Plan

The orchestrator model decomposes your high-level goal into disjoint, well-scoped subtasks. Each subtask has:

- A clear description of what to accomplish
- Expected inputs and outputs
- Success criteria

You will see:

```
🐝 PERP Swarm Activated
Planning with: gemma4:latest
Workers: 4 subtasks identified
```

The planner selects the best available model for orchestration (preferring high-reliability models with large context windows).

### 2. Execute

Subtasks are dispatched to worker threads. Each worker:

- Gets its own copy of the project context
- Has access to all tools (file ops, web, shell)
- Runs independently and in parallel
- Reports artifacts (files created/modified) back to the controller

```
  ✅ Agent [1] Completed: Research psutil-free system metrics
  ✅ Agent [2] Completed: Create dashboard layout module
  ✅ Agent [3] Completed: Create metrics collection module
  ✅ Agent [4] Completed: Wire CLI entry point
```

Workers execute concurrently on a background thread pool managed by a semaphore to prevent CPU overload.

### 3. Reflect

A dedicated reflection pass reviews all modified files, inspects tool outputs, and compares results against the original goals. If validation fails:

- Correction loops are generated
- Failed subtasks are retried (up to 3 attempts)
- The planner may decompose a failed task into smaller pieces

### 4. Persist

Once validated, changes are committed:

- Modified files are written to disk atomically
- Project memory (`context.md`) is updated with what was accomplished
- Session history records the full agent trace
- Token usage is logged for telemetry

```
✨ PERP Swarm Process Finished Successfully!
```

---

## Monitoring Agents

### Check status

While an agent is running:

```
/status
```

Shows active workers, their current subtask, and progress.

### Pause execution

```
/pause
```

Suspends worker dispatch. Currently running workers finish their task, but no new subtasks are started.

### Resume

```
/resume
```

Continues dispatching remaining subtasks.

### Stop completely

```
/stop
```

Cancels the swarm. Currently running workers are allowed to finish (graceful), but results are discarded and no persist happens.

---

## Dynamic Agent Escalation

Not every complex task requires you to type `/agent`. Kognisant detects multi-step tasks automatically and offers to escalate.

### What triggers auto-escalation:

The system analyzes your message for patterns that indicate multi-step work:

- Multiple distinct actions ("research X and then implement Y")
- Keywords suggesting broad scope ("comprehensive", "full suite", "all modules")
- Requests that combine investigation with implementation
- Tasks mentioning multiple files or components

When detected, the Plan phase upgrades the classification from COMPLEX to AUTONOMOUS:

```
📋 AUTONOMOUS → delegating to agent swarm
  Detected multi-step task requiring parallel execution
```

### Manual vs. automatic:

| Scenario | What happens |
|:---|:---|
| `/agent build a test suite` | Immediate swarm dispatch (explicit) |
| "build a comprehensive test suite for all modules" | Auto-detected, escalated to swarm |
| "fix the typo on line 5" | Stays as COMPLEX, single execution |
| "explain how the auth works" | Stays as CONTEXT, simple response |

You can always force the swarm with `/agent` even for simple tasks. And you can always prevent escalation by keeping your request focused on a single action.

---

## Artifact Tracking

Every agent worker reports what it created or modified:

```
  Artifacts:
    ✅ Created: tests/test_auth.py
    ✅ Created: tests/test_rate_limiter.py
    ✏️  Modified: src/auth/middleware.py
    ✏️  Modified: README.md
```

After the swarm finishes, you can review all changes:

```
/files         See new files in the project
/context       See updated memory with what was done
/read <path>   Inspect any file the agent created
```

---

## Token Usage Per Worker

Each worker tracks its own token consumption:

```
  Worker [1]: 1,200 in / 340 out (2.1s)
  Worker [2]: 2,400 in / 890 out (4.7s)
  Worker [3]: 1,800 in / 520 out (3.2s)
  Worker [4]: 900 in / 210 out (1.1s)
  Total: 6,300 in / 1,960 out
```

This helps you understand which subtasks are expensive and whether the parallelization saved time vs. sequential execution.

---

## Best Practices

### Write clear task descriptions

Bad: "fix everything"
Good: "Fix the authentication tests that are failing due to the JWT refactor in auth/middleware.py"

### Provide context before launching

Load relevant files first:

```
/read src/auth/middleware.py
/agent now refactor this to use async/await throughout
```

The agent workers inherit your conversation context, so pre-loaded files are available to them.

### Use specs for large features

If your task spans multiple days of work, consider using [Spec-Driven Development](spec-driven-development.md) instead. Specs give the agent a structured plan to execute against, with checkpoints and resumability.

### Check results after completion

Always review what the agent produced:

```
/context
```

If something needs adjustment, you can ask follow-up questions in normal chat mode, or launch another `/agent` pass with more specific instructions.

---

## How Model Selection Works for Agents

The swarm uses a model selection strategy:

1. **Planner model** - Prefers the highest-reliability model with the largest context window from your pool
2. **Worker models** - Uses the active model by default, but can distribute across multiple models if available
3. **Fallback** - If a worker's model fails, it retries with the next best model

Models that have tripped circuit breakers are excluded from worker assignment. This means unreliable models do not slow down your swarm.
