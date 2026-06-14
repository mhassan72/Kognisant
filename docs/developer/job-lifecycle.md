# Job Lifecycle

Complete documentation of job types, state transitions, and execution flows.

## Job Types

Kognisant supports three job types, each with different execution semantics:

| Type | Description | Restart Behavior | Timeout |
|------|-------------|------------------|---------|
| `scheduled` | Fires on a cron schedule | No restart; re-fires on next cron match | 3600s (1 hour) |
| `persistent` | Runs continuously | Auto-restarts on crash (5s delay) | None (runs indefinitely) |
| `agent` | PERP orchestration task | No restart | 1800s (30 minutes) |

### Scheduled Jobs

Scheduled jobs are designed for periodic tasks (backups, test suites, health checks). They execute once per cron match, run to completion (or timeout), and wait for the next scheduled time.

```bash
kognisant job add --name nightly-tests --script run-tests.py --type scheduled --cron "0 2 * * *"
```

### Persistent Jobs

Persistent jobs are designed for long-running services (bots, monitors, servers). They run continuously and auto-restart on crash, with crash-loop detection to prevent infinite restart cycles.

```bash
kognisant job add --name telegram-bot --script bot.py --type persistent
```

### Agent Jobs

Agent jobs invoke the PERP orchestration pipeline (`perp_orchestrate()`) for autonomous AI-driven tasks. They use a project root as working directory context.

```bash
# Created programmatically via the schedule_job tool:
# schedule_job(name="refactor-auth", type="agent", task="Refactor auth module", project_root="/path/to/project")
```

## State Machine

```
                                    ┌───────────────────────────────┐
                                    │        TERMINAL STATES         │
                                    │  (no further transitions)      │
                                    │                               │
                                    │  ┌───────────┐ ┌──────────┐  │
                                    │  │ completed │ │  failed  │  │
                                    │  └───────────┘ └──────────┘  │
                                    │  ┌───────────┐ ┌──────────┐  │
                                    │  │ cancelled │ │crash_loop│  │
                                    │  └───────────┘ └──────────┘  │
                                    └───────────────────────────────┘
                                              ▲   ▲   ▲   ▲
                                              │   │   │   │
┌─────────┐     ┌───────────┐     ┌──────────┴───┴───┴───┴────────┐
│ pending │────▶│ scheduled │────▶│           running              │
└─────────┘     └───────────┘     └────────────────────────────────┘
     │               │                    │
     │               │                    │ (persistent only, on non-zero exit)
     │               │                    ▼
     │               │              ┌──────────┐
     │               │              │ restart  │ (5s delay, then back to running)
     │               │              └──────────┘
     │               │                    │
     │               │                    │ (after 5 restarts in 5 minutes)
     │               │                    ▼
     │               │              ┌──────────┐
     │               └──────────────│crash_loop│
     │                              └──────────┘
     │
     └─── (daemon picks up) ──▶ running
```

### State Definitions

| State | Description |
|-------|-------------|
| `pending` | Job added to queue, waiting for daemon to pick up |
| `scheduled` | Scheduled job waiting for next cron match |
| `running` | Subprocess is actively executing |
| `completed` | Exited with code 0 (scheduled/agent) or intentional exit (persistent) |
| `failed` | Exited with non-zero code, broken pipe, timeout, or other error |
| `cancelled` | User explicitly cancelled via CLI/chat |
| `crash_loop` | Persistent job crashed too many times (5 in 5 minutes) |

### Valid State Transitions

| From | To | Trigger |
|------|----|---------|
| `pending` | `running` | Daemon picks up and spawns process |
| `pending` | `cancelled` | User cancels before execution |
| `scheduled` | `running` | Cron expression matches current time |
| `scheduled` | `cancelled` | User cancels |
| `running` | `completed` | Process exits with code 0 |
| `running` | `failed` | Process exits non-zero, timeout, broken pipe, PID reuse |
| `running` | `cancelled` | User sends cancel (SIGTERM) |
| `running` | `running` | Persistent job restart after crash (5s delay) |
| `running` | `crash_loop` | Persistent job: 5 restarts within 5 minutes |
| `completed` | `scheduled` | (Scheduled jobs only) Reset after completion for next cron cycle |

### Cancellable vs Terminal States

```
Cancellable: {pending, scheduled, running}
Terminal:    {completed, failed, cancelled, crash_loop}
```

Attempting to cancel a job in a terminal state produces an error:
```
Error: state - Job 'foo' is in 'completed' state and cannot be cancelled.
```

## Scheduled Job Execution Flow

```
Daemon poll cycle:
│
├─ CronParser.matches(job.cron_expression, current_utc_time)?
│   │
│   ├─ NO → skip (check again next cycle)
│   │
│   └─ YES:
│       │
│       ├─ 1. Verify script exists and passes symlink check
│       │
│       ├─ 2. Update job state: "scheduled" → "running"
│       │     Set: pid, pid_started_at, last_run_at, run_count += 1
│       │
│       ├─ 3. ProcessManager.spawn(script_path, env_vars, {}, cwd=None)
│       │     - Creates subprocess with stdout/stderr PIPE
│       │     - Starts 2 StreamReader threads
│       │
│       ├─ 4. Monitor process (each poll cycle):
│       │     │
│       │     ├─ Still running + elapsed < 3600s? → continue monitoring
│       │     │
│       │     ├─ Still running + elapsed ≥ 3600s? → TIMEOUT
│       │     │     kill_gracefully(pid, timeout=10)
│       │     │     state → "failed", error = "Timeout (3600s)"
│       │     │
│       │     ├─ Exited with code 0?
│       │     │     state → "completed"
│       │     │     last_exit_code = 0
│       │     │     state → "scheduled" (reset for next cron match)
│       │     │
│       │     └─ Exited with non-zero code?
│       │           state → "failed"
│       │           last_exit_code = N
│       │           state → "scheduled" (reset for next cron match)
│       │
│       └─ 5. join(timeout=2) on StreamReader threads
│             (flush remaining log output)
```

### Run Count Tracking

The `run_count` field is incremented each time a scheduled job is spawned. Combined with `last_exit_code`, this gives a complete execution history view in `job list`.

## Persistent Job Execution Flow

```
Daemon picks up persistent job in "pending" state:
│
├─ 1. Verify script + symlink check
│
├─ 2. Update: state → "running", set pid/pid_started_at
│
├─ 3. ProcessManager.spawn(...)
│
├─ 4. Monitor continuously:
│     │
│     ├─ Process running? → continue
│     │
│     ├─ Process exited with code 0?
│     │     → state = "completed" (intentional shutdown)
│     │     → Do NOT restart
│     │
│     └─ Process exited with non-zero code?
│           → Check crash loop detection
│           │
│           ├─ Crash loop detected (5 crashes in 5 minutes)?
│           │     → state = "crash_loop"
│           │     → Log error: "Job entered crash loop"
│           │     → Do NOT restart
│           │
│           └─ Not in crash loop:
│                 → Wait 5 seconds (restart delay)
│                 → restart_count += 1
│                 → Record timestamp in restart_timestamps[]
│                 → Re-spawn process
│                 → Update pid/pid_started_at
│                 → Log: "Restarted job after crash (restart #{N})"
│
└─ END (only on completion, cancellation, or crash loop)
```

### Crash Loop Detection

A crash loop is detected when a persistent job has 5 or more restarts within a 5-minute window:

```python
def _is_crash_loop(restart_timestamps: list[str]) -> bool:
    """Check if recent restarts indicate a crash loop."""
    if len(restart_timestamps) < 5:
        return False
    recent = [ts for ts in restart_timestamps
              if (now - parse_iso(ts)).total_seconds() < 300]
    return len(recent) >= 5
```

### Exit Code Semantics for Persistent Jobs

| Exit Code | Meaning | Daemon Action |
|-----------|---------|---------------|
| 0 | Intentional completion | State → "completed", no restart |
| Non-zero | Crash/error | Restart after 5s delay (unless crash loop) |

This means scripts can signal "I'm done, stop restarting me" by exiting with code 0.

## Agent Job Execution Flow

```
Daemon picks up agent job:
│
├─ 1. Determine working directory:
│     cwd = job["project_root"] or os.path.expanduser("~")
│
├─ 2. Update: state → "running"
│
├─ 3. Invoke perp_orchestrate(
│         task=job["task"],
│         project_info=load_project_info(cwd),
│         compiled_models=get_compiled_models()
│     )
│     │
│     ├─ PERP Pipeline:
│     │   Plan → Execute (threaded subtasks) → Reflect → Persist
│     │
│     ├─ Timeout: 1800 seconds (30 minutes)
│     │   If exceeded: raise, state → "failed"
│     │
│     ├─ Success:
│     │   state → "completed"
│     │   Record completion timestamp
│     │
│     └─ Failure (exception from perp_orchestrate):
│           state → "failed"
│           error = str(exception)
│
└─ END
```

### Agent Job Context

Agent jobs receive the full PERP orchestration context:
- **task**: The high-level task description (e.g., "Write test suite for auth module")
- **project_root**: The filesystem path used as working directory. If null, defaults to `$HOME`.
- **compiled_models**: Available model configurations for the agent to use

## Graceful Shutdown Sequence

When the daemon receives SIGTERM (via `kognisant daemon stop` or `/daemon stop`):

```
SIGTERM received → _shutdown_flag = True
│
├─ 1. Break out of sleep loop (next 500ms check)
│
├─ 2. For each job in "running" state:
│     │
│     ├─ Send SIGTERM to subprocess
│     │
│     ├─ Wait up to 10 seconds for graceful exit
│     │     (poll every 100ms)
│     │
│     ├─ Process exited within 10s?
│     │     → join StreamReader threads (timeout=2)
│     │     → Update job state (completed/failed based on exit code)
│     │
│     └─ Process still running after 10s?
│           → Send SIGKILL (force kill)
│           → join StreamReader threads (timeout=2)
│           → Update job state: "failed", error = "Killed during shutdown"
│
├─ 3. Close all open file handles
│
├─ 4. Remove PID file
│
├─ 5. Log "Daemon stopped"
│
└─ 6. Exit
```

### Shutdown Timing Budget

```
Total worst-case shutdown time = N_jobs × (10s SIGTERM wait + 2s thread join)
                                + 1s (cleanup overhead)

For 5 running jobs: ~61 seconds maximum
```

## SIGHUP Responsiveness

The daemon's sleep interval is implemented as a series of 500ms naps:

```python
def _interruptible_sleep(seconds: float) -> None:
    """Sleep in 500ms increments, checking flags each tick."""
    remaining = seconds
    while remaining > 0 and not _shutdown_flag:
        if _reload_flag:
            _reload_flag = False
            return  # Break out immediately
        time.sleep(min(0.5, remaining))
        remaining -= 0.5
```

This means:
- SIGHUP (reload) takes effect within 500ms
- SIGTERM (shutdown) takes effect within 500ms
- No need for signal interrupting sleep (which is platform-fragile)

### What SIGHUP Does

When SIGHUP is received:
1. The daemon breaks out of its current sleep
2. Immediately begins a new polling cycle
3. Re-reads the job queue from disk (picking up any external modifications)

This is useful for:
- Forcing immediate execution of a newly-added job
- Picking up configuration changes without waiting for the next poll cycle

## Cross-References

- [Architecture](architecture.md) — System overview and daemon process model
- [Execution Engine](execution-engine.md) — Atomic writes and PID reuse protection
- [CLI Reference](cli-reference.md) — Commands that create and manage jobs
- [Cron Scheduling](cron-scheduling.md) — CronParser details for scheduled jobs
