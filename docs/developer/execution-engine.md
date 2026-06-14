# Execution Engine

Deep technical documentation of the hardened execution engine internals. This covers the mechanisms that ensure data integrity, crash recovery, and reliable process management.

## Atomic Write Sequence

Every write to `jobs.json` follows a crash-safe atomic sequence. At no point can a power failure or process kill leave the file in a corrupted or partial state.

```
_atomic_save(data):
│
├─ 1. Create temp file in same directory
│     tmp_fd, tmp_path = tempfile.mkstemp(dir=queue_dir, prefix="jobs_", suffix=".tmp")
│
├─ 2. Write serialized JSON
│     json.dump(data, f, indent=2)
│
├─ 3. Flush user-space buffers
│     f.flush()
│
├─ 4. Sync to disk (ensures data is on physical media)
│     os.fsync(fd)
│
├─ 5. Set restrictive permissions before the file becomes visible
│     os.chmod(tmp_path, 0o600)
│
├─ 6. Atomic rename (POSIX guarantees this is atomic on same filesystem)
│     os.rename(tmp_path, QUEUE_PATH)
│     │
│     ├─ SUCCESS:
│     │   │
│     │   ├─ 7. Sync directory entry (makes rename durable)
│     │   │     dirfd = os.open(queue_dir, os.O_RDONLY)
│     │   │     os.fsync(dirfd)
│     │   │     os.close(dirfd)
│     │   │
│     │   ├─ 8. Create backup copy
│     │   │     shutil.copy2(QUEUE_PATH, BACKUP_PATH)
│     │   │
│     │   ├─ 9. Set backup permissions
│     │   │     os.chmod(BACKUP_PATH, 0o600)
│     │   │
│     │   └─ 10. Sync backup to disk
│     │         bak_fd = os.open(BACKUP_PATH, os.O_RDONLY)
│     │         os.fsync(bak_fd)
│     │         os.close(bak_fd)
│     │
│     └─ FAILURE (OSError):
│         ├─ Remove temp file: os.unlink(tmp_path)
│         └─ Existing QUEUE_PATH remains unchanged
│
└─ END
```

### Why This Order Matters

1. **Write to temp first**: If we crash during the write, only the temp file is affected — the real `jobs.json` is untouched.
2. **fsync before rename**: Ensures the data is actually on disk, not just in the OS page cache.
3. **chmod before rename**: The file has correct permissions before it becomes visible to other processes.
4. **Atomic rename**: On POSIX, `rename()` is guaranteed atomic — readers see either the old file or the new file, never a partial state.
5. **Directory fsync**: Without this, a crash could lose the directory entry update even though the file data is on disk.
6. **Backup after success**: The `.bak` file always represents the last known-good state.

### Failure Modes Covered

| Failure Point | Result |
|--------------|--------|
| Crash during temp write | Orphaned `.tmp` file, `jobs.json` unchanged |
| Crash after fsync, before rename | Same as above |
| Crash during rename | POSIX guarantees atomicity — impossible to be partial |
| Crash after rename, before dir fsync | File exists but may not survive reboot on some filesystems (unlikely but possible) |
| Crash during backup copy | Primary is safe; backup may be stale (recovered on next successful write) |

## Recovery Decision Tree

The `_load_raw()` method implements a multi-level recovery strategy:

```
_load_raw():
│
├─ Does jobs.json exist?
│   │
│   ├─ YES → Parse JSON
│   │   │
│   │   ├─ Valid JSON?
│   │   │   │
│   │   │   ├─ YES → Has "schema_version" key?
│   │   │   │   │
│   │   │   │   ├─ YES → schema_version ≤ CURRENT_SCHEMA_VERSION?
│   │   │   │   │   │
│   │   │   │   │   ├─ YES → _migrate_if_needed(data) → return data
│   │   │   │   │   │         (applies pending migrations if version < current)
│   │   │   │   │   │
│   │   │   │   │   └─ NO → raise ValueError
│   │   │   │   │           "Unknown schema version N. Refusing to process.
│   │   │   │   │            This file may be from a newer version of Kognisant."
│   │   │   │   │
│   │   │   │   └─ NO → Is it a bare list? (legacy format)
│   │   │   │       │
│   │   │   │       ├─ YES → Wrap as {"schema_version": 1, "jobs": data}
│   │   │   │       │         _atomic_save(wrapped) → return wrapped
│   │   │   │       │
│   │   │   │       └─ NO → Treat as corrupted → try .bak
│   │   │   │
│   │   │   └─ NO (not dict/list) → try .bak
│   │   │
│   │   └─ NO (JSONDecodeError) → try .bak
│   │
│   └─ [try .bak path]:
│       │
│       ├─ Does .bak exist + valid + recognized version?
│       │   ├─ YES → log WARNING "Recovered from backup"
│       │   │         _atomic_save(data) → return data
│       │   └─ NO → fall through
│       │
│       └─ Initialize empty: {"schema_version": 1, "jobs": []}
│          log ERROR "Both primary and backup missing/corrupted. Data loss."
│          _atomic_save(empty) → return empty
│
└─ NO (jobs.json missing):
    │
    ├─ Does .bak exist + valid?
    │   ├─ YES → log WARNING "Primary missing, restored from backup"
    │   │         _atomic_save(data) → return data
    │   └─ NO → Initialize empty (same as above)
    │
    └─ END
```

### Key Design Decisions

- **Unknown version = hard fail**: Never silently downgrade or process data from a newer Kognisant version.
- **Legacy bare array = auto-migrate**: Seamlessly upgrades pre-versioned files.
- **Backup recovery = always re-saves**: After recovering from `.bak`, immediately write a new atomic primary to ensure consistency.
- **Empty initialization = last resort**: Only when both files are unrecoverable. Logged as an error for visibility.

## Schema Versioning and MigrationRegistry

### Version Format

```json
{
  "schema_version": 1,
  "jobs": [...]
}
```

The `schema_version` is a monotonically increasing integer. Each version increment represents a structural change to the job schema.

### MigrationRegistry

```python
class MigrationRegistry:
    _migrations: dict[int, Callable[[dict], dict]] = {}

    @classmethod
    def register(cls, from_version: int):
        """Decorator to register a migration from version N to N+1."""
        def decorator(fn):
            cls._migrations[from_version] = fn
            return fn
        return decorator

    @classmethod
    def apply_pending(cls, data: dict, save_fn: Callable[[dict], None]) -> dict:
        """Apply all pending migrations sequentially."""
        current = data["schema_version"]
        while current < CURRENT_SCHEMA_VERSION:
            if current not in cls._migrations:
                raise ValueError(f"No migration for version {current} → {current + 1}")
            data = cls._migrations[current](data)
            save_fn(data)  # atomic save preserves pre-migration in .bak
            current = data["schema_version"]
        return data
```

### Migration Rules

1. **Forward-only**: Migrations go from N to N+1. No downgrades.
2. **Pure functions**: Each migration returns a new dict, never modifies in-place.
3. **Atomic persistence**: Each migration step uses `_atomic_save`, so `.bak` contains the pre-migration state.
4. **Missing migration = hard fail**: If a required migration function isn't registered, raise immediately.
5. **Increment by exactly 1**: Each migration must set `schema_version = from_version + 1`.

### Example Migration

```python
@MigrationRegistry.register(from_version=1)
def migrate_v1_to_v2(data: dict) -> dict:
    """Add scheduler_policy field to all jobs."""
    for job in data["jobs"]:
        job.setdefault("scheduler_policy", "skip")
        job.setdefault("last_exit_code", None)
        job.setdefault("run_count", 0)
    data["schema_version"] = 2
    return data
```

## FileLock and `_locked_modify` Pattern

### FileLock

Advisory file locking using `fcntl.flock()`:

```python
class FileLock:
    def __init__(self, lock_path: str, timeout: float = 5.0):
        self.lock_path = lock_path
        self.timeout = timeout
        self._fd = None

    def acquire(self) -> bool:
        self._fd = open(self.lock_path, 'w')
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except (IOError, OSError):
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Could not acquire file lock within {self.timeout} seconds"
                    )
                time.sleep(0.05)

    def release(self) -> None:
        if self._fd:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._fd.close()
            self._fd = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()
        return False
```

### `_locked_modify` Pattern

All state mutations use this pattern to prevent lost updates:

```python
def _locked_modify(self, fn: Callable[[list[dict]], list[dict]]) -> None:
    """Hold advisory lock across entire read-modify-write cycle.

    1. Acquire exclusive lock on jobs.lock
    2. Load current state from jobs.json
    3. Apply mutation function: fn(jobs_list) → modified_jobs_list
    4. Atomic save the modified state
    5. Release lock

    The lock is held for the entire duration — no other process can
    read or write jobs.json between our load and save.
    """
    with FileLock(self.LOCK_PATH, timeout=5.0):
        data = self._load_raw()
        data["jobs"] = fn(data["jobs"])
        self._atomic_save(data)
```

### Why Advisory Locking?

- **Single-user model**: Kognisant assumes one user with one daemon. Advisory locks prevent race conditions between the CLI and daemon.
- **Non-blocking check**: Uses `LOCK_NB` with polling to implement timeout behavior.
- **No mandatory locks**: Advisory locks are cooperative — all participants must use them. Since we control both the CLI and daemon, this is sufficient.

## StreamReader Thread Architecture

```
                    ┌─────────────────────┐
                    │   ProcessManager    │
                    │     .spawn()        │
                    └─────────┬───────────┘
                              │ creates Popen + 2 StreamReaders
                              ▼
               ┌──────────────────────────────┐
               │        subprocess.Popen       │
               │   stdout=PIPE, stderr=PIPE   │
               └──────┬──────────────┬────────┘
                      │              │
            ┌─────────▼────┐  ┌─────▼──────────┐
            │ StreamReader  │  │  StreamReader   │
            │ (stdout)      │  │  (stderr)       │
            │ daemon=True   │  │  daemon=True    │
            │ prefix=""     │  │  prefix="[ERROR]"│
            └──────┬────────┘  └──────┬──────────┘
                   │                   │
                   │  for line in iter(pipe.readline, b""):
                   │      decode utf-8, append to log
                   │                   │
                   ▼                   ▼
            ┌────────────────────────────────┐
            │  ~/.kognisant_core/logs/       │
            │    {job_name}.log              │
            └────────────────────────────────┘
```

### Key Properties

- **`daemon=True`**: Threads don't prevent process shutdown. If the daemon exits, these threads are automatically cleaned up.
- **`iter(pipe.readline, b"")`**: Blocks until a line is available or EOF. More reliable than `for line in pipe` which can buffer unpredictably.
- **UTF-8 decoding with `errors="replace"`**: Prevents crashes on binary output from scripts.
- **On subprocess exit**: `join(timeout=2)` gives threads time to flush remaining buffered lines.

### Broken Pipe Handling

```python
def run(self) -> None:
    try:
        for line in iter(self.pipe.readline, b""):
            decoded = line.decode("utf-8", errors="replace")
            self._append_line(decoded)
    except (BrokenPipeError, IOError) as e:
        if self._on_broken_pipe:
            self._on_broken_pipe(str(e))
```

When a broken pipe is detected:
1. The callback marks the job as `"failed"` with a "broken pipe" error message
2. `ProcessManager.kill_gracefully(pid)` terminates the subprocess
3. The failure event is logged to `daemon.log`

## FD Cleanup in Forked Child

When the daemon forks, the child process inherits all open file descriptors from the parent. This is problematic because:
- Inherited FDs to terminal would allow the daemon to accidentally write to the user's terminal
- Socket FDs could cause resource leaks
- Lock FDs could cause deadlocks

### Cleanup Sequence

```python
# In the forked child process:

# 1. Create new session (detach from controlling terminal)
os.setsid()

# 2. Close ALL inherited file descriptors above stderr
#    FDs 0, 1, 2 are handled separately in step 3
os.closerange(3, os.sysconf("SC_OPEN_MAX"))

# 3. Redirect standard streams to /dev/null
devnull_fd = os.open(os.devnull, os.O_RDWR)
os.dup2(devnull_fd, 0)  # stdin
os.dup2(devnull_fd, 1)  # stdout
os.dup2(devnull_fd, 2)  # stderr
if devnull_fd > 2:
    os.close(devnull_fd)
```

### Why `SC_OPEN_MAX`?

`os.sysconf("SC_OPEN_MAX")` returns the maximum number of file descriptors a process can have (typically 1024 or higher). This ensures we close everything, not just FDs we know about.

## Clock Jump Detection and Scheduler Policies

### Detection Mechanism

```python
_last_tick = time.monotonic()
POLL_INTERVAL = 15  # seconds
JUMP_THRESHOLD = 2 * POLL_INTERVAL  # 30 seconds

# Each polling cycle:
now = time.monotonic()
elapsed = now - _last_tick
_last_tick = now

if elapsed > JUMP_THRESHOLD:
    # Clock jump detected!
    handle_clock_jump(elapsed)
```

### Why `time.monotonic()`?

- `time.monotonic()` is not affected by NTP adjustments or manual clock changes
- If monotonic elapsed time exceeds 30s between cycles that should take ~15s, it means the system was suspended (sleep/hibernate)
- We compare against wall-clock time to determine which scheduled jobs were missed

### Scheduler Policies

**`skip` (default)**:
```python
# Discard all missed executions during the jump period
for job in get_due_in_range(jump_start, jump_end):
    logger.info(f"Clock jump: skipping missed execution for {job['name']}")
# Resume normal scheduling from current time
```

**`catchup_once`**:
```python
# Fire each missed job exactly once
for job in get_due_in_range(jump_start, jump_end):
    logger.info(f"Clock jump: catchup execution for {job['name']}")
    spawn_job(job)  # execute once regardless of how many intervals were missed
```

## Orphan Cleanup with PID Reuse Protection

On daemon startup, jobs marked as "running" need verification — their processes may have died while the daemon was down.

### The PID Reuse Problem

PIDs are recycled by the OS. If job "my-bot" had PID 12345, and that process died, the OS might assign PID 12345 to an unrelated process (like your web browser). Blindly sending SIGTERM to PID 12345 would kill your browser.

### Solution: Process Creation Time Validation

```python
def orphan_cleanup():
    for job in get_jobs_in_state("running"):
        pid = job["pid"]

        if not ProcessManager.is_alive(pid):
            # Process is dead — safe to mark as failed
            update_status(job["name"], "failed", error="Orphaned process not found")
            continue

        # Process IS alive — but is it OUR process?
        actual_start_time = ProcessManager.get_start_time(pid)
        expected_start_time = job["pid_started_at"]

        if actual_start_time != expected_start_time:
            # PID was reused by a different process!
            # Do NOT send any signal — just mark the job as failed
            update_status(job["name"], "failed",
                         error="PID reused by another process")
            job["pid"] = None
        else:
            # Same process — it's still running, leave it alone
            pass
```

### Platform-Specific `get_start_time()`

**macOS**:
```python
result = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)],
                       capture_output=True, text=True)
# Returns: "Mon Jun 15 10:30:01 2025"
```

**Linux**:
```python
with open(f"/proc/{pid}/stat") as f:
    fields = f.read().split()
    starttime_ticks = int(fields[21])  # field 22 (0-indexed: 21)
# Convert clock ticks to timestamp using os.sysconf("SC_CLK_TCK")
```

## Broken Pipe Detection

A broken pipe occurs when:
- The subprocess closes its stdin/stdout while we're still writing/reading
- The subprocess crashes and the pipe's read end is closed

### Detection Points

1. **StreamReader thread**: Catches `BrokenPipeError` or `IOError` with `errno.EPIPE` during `pipe.readline()`
2. **Daemon main loop**: Detects when `proc.poll()` returns non-None unexpectedly

### Response

```python
def _on_broken_pipe(self, error_msg: str):
    """Called by StreamReader when pipe breaks."""
    job_queue.update_status(self.job_name, "failed",
                           error=f"Broken pipe: {error_msg}")
    ProcessManager.kill_gracefully(self.pid)
    logger.error(f"Broken pipe for job '{self.job_name}': {error_msg}")
```

## Daemon Start Race Prevention

Two simultaneous `kognisant daemon start` commands must not produce two daemon processes.

### Mechanism: `O_CREAT|O_EXCL`

```python
def _create_pid_file(pid: int) -> None:
    """Atomically create PID file. Fails if another process created it first."""
    try:
        fd = os.open(PID_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.write(fd, str(pid).encode())
        os.close(fd)
    except FileExistsError:
        raise RuntimeError("Another daemon instance is starting. Aborting.")
```

### Full Startup Sequence

```
1. Check if PID_FILE exists
   ├─ YES → Read PID from file
   │   ├─ Process alive? → "Daemon already running (PID N)"
   │   └─ Process dead? → Remove stale PID file, continue
   └─ NO → continue

2. Fork child process

3. Child: Create PID file with O_CREAT|O_EXCL
   ├─ SUCCESS → Write our PID, continue to main loop
   └─ FAILURE (FileExistsError) → Another instance won the race, abort
```

The `O_EXCL` flag makes the `open()` call fail atomically if the file already exists — even if another process created it in the nanoseconds between our stale check and our create attempt.

## RotatingFileHandler for daemon.log

```python
import logging.handlers

handler = logging.handlers.RotatingFileHandler(
    LOG_FILE,
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=3
)
```

This creates: `daemon.log`, `daemon.log.1`, `daemon.log.2`, `daemon.log.3`

When `daemon.log` reaches 10MB, it's renamed to `daemon.log.1` (pushing older logs down) and a fresh `daemon.log` is created.

## Job Log Rotation

Per-job logs use a simpler rename-then-open strategy:

```python
def _rotate_job_log(log_path: str) -> None:
    """Rotate job log if it exceeds 10MB."""
    if os.path.getsize(log_path) > 10 * 1024 * 1024:
        rotated = log_path + ".1"
        os.rename(log_path, rotated)
        # New writes will create a fresh log_path
```

Key difference from `RotatingFileHandler`: The daemon holds no persistent file handles to job logs. Each `StreamReader._append_line()` opens, writes, and closes the log file. This means rotation via rename is safe — no write-after-rename race.

## Cross-References

- [Architecture](architecture.md) — System overview and module relationships
- [Job Lifecycle](job-lifecycle.md) — Job state machine and execution flows
- [Security](security.md) — Permission model and symlink protection
- [Testing](testing.md) — How these components are tested
