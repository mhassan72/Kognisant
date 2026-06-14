# Architecture

## High-Level System Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        USER / AGENT INTERFACE                              │
├──────────────────────────────────────────────────────────────────────────┤
│  CLI (main.py)                 Chat (chat.py)          Agent Tools         │
│  ─────────────                 ──────────────          ───────────         │
│  kognisant daemon start/stop   /daemon start/stop      schedule_job()      │
│  kognisant job add/list/edit   /jobs, /job stop        cancel_job()        │
│  kognisant job logs --follow   /job logs <name>        remove_job()        │
│  kognisant init/chat/spec      /agent <task>           list_jobs()         │
│  kognisant status              /model, /context        create_script()     │
└────────────────────────────────────┬─────────────────────────────────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    │                │                │
                    ▼                ▼                ▼
┌─────────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐
│  config.py           │  │  network.py       │  │  agents.py                │
│  ─────────           │  │  ──────────       │  │  ─────────               │
│  Global Core init    │  │  API transport    │  │  PERP orchestration      │
│  Project discovery   │  │  Exponential      │  │  Subtask threading       │
│  Model pool mgmt     │  │  backoff/retry    │  │  Reflection loops        │
│  Membrain loading    │  │  Ollama detect    │  │  Memory persistence      │
└─────────────────────┘  └──────────────────┘  └──────────────────────────┘
                    │                                     │
                    ▼                                     ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                    JOBS MODULE (jobs.py) - Versioned State Layer           │
├──────────────────────────────────────────────────────────────────────────┤
│  JobQueue              CronParser             FileLock                     │
│  ─────────             ──────────             ────────                    │
│  _locked_modify(fn)    matches(expr, dt)      acquire(timeout=5)         │
│  _atomic_save(data)    next_run(expr, after)  release()                  │
│  _load_raw()           validate(expr)         __enter__/__exit__         │
│  _recover_from_backup  can_match_within_days                             │
│  add/remove/update     MigrationRegistry                                 │
│                        ──────────────────                                 │
│  Storage: {"schema_version": 1, "jobs": [...]}                            │
│  Lock: fcntl.flock(LOCK_EX) on jobs.lock                                 │
│  Backup: jobs.json.bak (copy after primary rename)                        │
└────────────────────────────────────┬─────────────────────────────────────┘
                                     │ polled every 15s
                                     ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                DAEMON MODULE (daemon.py) - Execution Layer                 │
├──────────────────────────────────────────────────────────────────────────┤
│  DaemonManager                    ProcessManager                          │
│  ──────────────                   ──────────────                          │
│  start() → fork + FD cleanup     spawn(path, env, ctx, cwd) → Popen     │
│  stop() → SIGTERM + wait         is_alive(pid) → bool                   │
│  restart() → stop + start        get_start_time(pid) → str              │
│  status() → health dict          kill_gracefully(pid, timeout=10)        │
│  is_running() → bool             check_symlink(path, dir) → bool        │
│                                                                           │
│  StreamReader (daemon threads)    _main_loop():                           │
│  ────────────────────────────       - orphan cleanup on start             │
│  stdout reader (daemon=True)        - clock jump detection                │
│  stderr reader (prefix=[ERROR])     - scheduler_policy eval               │
│  join(timeout=2) on exit            - SIGHUP check @ 500ms               │
│                                     - broken pipe detection               │
│                                                                           │
│  Logging: RotatingFileHandler(maxBytes=10MB, backupCount=3)               │
│  PID: O_CREAT|O_EXCL race prevention                                     │
│  FD: os.closerange(3, SC_OPEN_MAX) + /dev/null redirect                  │
└────────────────────────────────────┬─────────────────────────────────────┘
                                     │ spawns subprocesses
                                     ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                  SCRIPTS + PERP - Execution Targets                        │
├──────────────────────────────────────────────────────────────────────────┤
│  scripts.py (atomic create)        agents.py                              │
│  ──────────────────────────        ─────────                              │
│  create_script() → atomic 2-phase  perp_orchestrate(task, project_info,  │
│  validate_script_name()              compiled_models)                      │
│  _resolve_script_path()                                                   │
│                                                                           │
│  Symlink protection: os.path.realpath() must resolve within               │
│  ~/.kognisant_core/scripts/                                               │
└──────────────────────────────────────────────────────────────────────────┘
```

## Module Responsibilities

### `main.py` - CLI Entry Point

The argparse-based command-line interface. Registers subcommands (`init`, `chat`, `greet`, `spec`, `daemon`, `job`, `status`, `awesome_feature`) and dispatches to handler functions. Handles argument validation and user-facing output formatting.

### `chat.py` - Interactive Chat Loop

Manages the multi-turn conversation session including:
- Slash command parsing and dispatch (`/help`, `/model`, `/agent`, `/jobs`, `/daemon`, etc.)
- Checkpoint-based rollback on API failures
- Session persistence to `.kognisant/history/`
- Model selection wizard
- Tool call processing loop (parse tool_calls → execute → append results → re-query)

### `config.py` - Configuration Management

Handles all configuration state:
- Global Core initialization (`~/.kognisant_core/` structure)
- Project discovery (`find_project_root()` walks up the directory tree)
- File scanning with exclusion patterns
- Model pool management (load, save, set default)
- Provider configuration

### `agents.py` - PERP Orchestration

The autonomous agent swarm engine:
- **Plan**: Decomposes tasks into phased subtasks via LLM
- **Execute**: Spawns threaded worker agents with semaphore-based concurrency control
- **Reflect**: Validates results against goals, generates correction loops
- **Persist**: Updates project Membrain with completed work

Uses `threading.Thread` with `daemon=True` for subtask workers. Local model tasks are throttled via a `threading.Semaphore(MAX_LOCAL_CONCURRENCY)`.

### `network.py` - API Transport

OpenAI-compatible HTTP client with:
- Exponential backoff retry (3 attempts, delays: 1s, 2s, 4s)
- Ollama native API auto-detection
- Llama.cpp native protocol support
- SSL context creation per request
- Response streaming support

### `tools.py` - Tool Specifications & Execution

Defines the OpenAI function-calling tool schemas and implements tool handlers:
- **Workspace tools**: `read_project_file`, `edit_project_file`, `list_project_files`, `create_project_file`, `create_project_directory`, `delete_project_path`
- **Web tools**: `browse_web_page`, `search_web`, `open_in_native_browser`
- **Job tools**: `schedule_job`, `cancel_job`, `remove_job`, `list_jobs`, `job_logs`
- **Script tools**: `create_script`, `read_script`, `edit_script`, `delete_script`, `list_scripts`

All file tools enforce workspace sandboxing via `os.path.realpath()`.

### `daemon.py` - Background Daemon

The forked background process that manages job execution:
- `DaemonManager`: Lifecycle (start/stop/restart/status), fork with FD cleanup, PID file with race prevention
- `ProcessManager`: Subprocess spawning with stream readers, PID validation, graceful termination
- `StreamReader`: Daemon threads for live stdout/stderr capture
- `_main_loop()`: Polling loop with orphan cleanup, clock jump detection, scheduler policy evaluation

### `jobs.py` - Versioned Job Queue

State management layer with crash-safe persistence:
- `JobQueue`: CRUD operations using `_locked_modify` for atomic read-modify-write
- `FileLock`: Advisory locking via `fcntl.flock()` with timeout
- `CronParser`: 5-field cron expression parsing and evaluation (UTC)
- `MigrationRegistry`: Forward-only schema migration framework
- Atomic write sequence with backup creation

### `scripts.py` - Script CRUD

Manages user scripts in `~/.kognisant_core/scripts/`:
- Atomic two-phase script creation (`.py.tmp` + `.json.tmp` → rename both)
- Script name validation
- Symlink containment via `os.path.realpath()` check

### `colors.py` - Terminal UI

ANSI terminal rendering utilities:
- True-color RGB fade-in logo animation
- Thread-safe Braille spinners
- Boxed input frame (collapsible)
- Color palette constants
- Terminal width detection

## Data Flow

### Chat Turn Flow

```
User Input → chat.py
  ├─ Slash command? → dispatch handler → return to prompt
  └─ Regular message:
       ├─ Append to messages[]
       ├─ checkpoint_idx = len(messages)
       ├─ Send to LLM via network.py
       │    ├─ Response has tool_calls?
       │    │    ├─ Execute each tool via tools.py
       │    │    ├─ Append tool results to messages[]
       │    │    └─ Re-query LLM (loop until no tool_calls)
       │    └─ Response is plain text → append assistant message
       ├─ Save session to .kognisant/history/
       └─ On failure: truncate messages[] to checkpoint_idx
```

### Daemon Polling Cycle

```
_main_loop() [every 15 seconds]:
  ├─ Detect clock jump (monotonic elapsed > 30s)
  │    ├─ skip policy: discard missed executions
  │    └─ catchup_once: fire each missed job once
  ├─ Get due scheduled jobs (CronParser.matches)
  │    └─ For each: spawn subprocess, update state to "running"
  ├─ Get pending persistent/agent jobs
  │    └─ For each: spawn subprocess or perp_orchestrate()
  ├─ Monitor running processes
  │    ├─ Exited normally → state = "completed"
  │    ├─ Exited non-zero → state = "failed" or restart (persistent)
  │    ├─ Broken pipe → terminate + state = "failed"
  │    └─ Timeout exceeded → SIGTERM + state = "failed"
  └─ Sleep in 500ms increments (check _shutdown_flag, _reload_flag)
```

## Threading Model

### Daemon Main Loop

The daemon runs a single-threaded main loop that polls every 15 seconds. Between polls, it sleeps in 500ms increments to remain responsive to SIGHUP (reload) and SIGTERM (shutdown).

### StreamReader Threads

Each spawned subprocess gets two daemon threads:
- **stdout reader**: Reads lines and appends to `{job_name}.log`
- **stderr reader**: Reads lines with `[ERROR] ` prefix, appends to same log

Both threads are created with `daemon=True` so they don't prevent process shutdown. On subprocess exit, `join(timeout=2)` is called to flush any remaining buffered output.

### Agent Worker Threads

When PERP orchestration runs (either in chat or as a daemon agent job), subtask agents are spawned as daemon threads. Local model tasks share a semaphore (`MAX_LOCAL_CONCURRENCY`) to prevent system overload. Results are collected via a shared `results_dict` and `threading.Lock`.

## Process Model

### Daemon Fork Sequence

```
Parent Process (CLI):
  1. Check for stale PID file → remove if dead
  2. os.fork() → child PID returned to parent
  3. Parent: write PID to file, print success, exit handler

Child Process (Daemon):
  1. os.setsid() → new session leader
  2. os.closerange(3, os.sysconf("SC_OPEN_MAX")) → close inherited FDs
  3. Redirect stdin/stdout/stderr to /dev/null
  4. Create PID file with O_CREAT|O_EXCL (atomic, race-free)
  5. Setup RotatingFileHandler for daemon.log
  6. Install signal handlers:
     - SIGTERM → set _shutdown_flag
     - SIGHUP → set _reload_flag
  7. Log "Daemon started" → enter _main_loop()
```

### Subprocess Spawning

```
ProcessManager.spawn(script_path, env, job_context, cwd):
  1. Verify symlink containment: realpath(script_path) ∈ scripts_dir/
  2. subprocess.Popen(
       [sys.executable, script_path],
       stdin=PIPE, stdout=PIPE, stderr=PIPE,
       env={**os.environ, **env},
       cwd=cwd or HOME
     )
  3. Write job_context JSON to proc.stdin, close stdin
  4. Start StreamReader(proc.stdout, log_path, prefix="")
  5. Start StreamReader(proc.stderr, log_path, prefix="[ERROR] ")
  6. Return Popen object
```

## File System Layout

```
~/.kognisant_core/
├── projects.json          # Global workspace registry
├── models_pool.json       # Model configurations + sticky default
├── providers.json         # Provider URLs and API keys
├── jobs.json              # Versioned job queue (0o600)
├── jobs.json.bak          # Most recent committed backup (0o600)
├── jobs.lock              # Advisory lock file (fcntl.flock)
├── daemon.pid             # Daemon PID file (O_CREAT|O_EXCL)
├── daemon.log             # Rotated daemon log (10MB × 3 backups)
├── skills/                # Transferable skill files (Markdown)
│   └── coding_standards.md
├── tools/                 # Global tool schemas + implementations
│   ├── tool_name.json     # OpenAI function-calling schema
│   └── tool_name.py       # Isolated subprocess implementation
├── scripts/               # User scripts executed by daemon
│   ├── script_name.py     # Script content
│   └── script_name.json   # Script metadata
└── logs/                  # Per-job log files
    ├── job_name.log       # Current log (rotated at 10MB)
    └── job_name.log.1     # Previous rotated log
```

## Cross-References

- [Execution Engine](execution-engine.md) - Atomic write and recovery internals
- [Job Lifecycle](job-lifecycle.md) - State machine and execution flows
- [Security](security.md) - Symlink containment and permission model
- [CLI Reference](cli-reference.md) - All commands and flags
